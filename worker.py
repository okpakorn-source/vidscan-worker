
# worker.py — Railway yt-dlp Worker
# รับ xhslink URL → ดาวน์โหลดวิดีโอ → ส่งกลับเป็น base64 หรือ URL

import os, json, base64, tempfile, subprocess, traceback
from flask import Flask, request, jsonify

app = Flask(__name__)
PORT = int(os.environ.get('PORT', 8000))

# ── health check ────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'VidScan yt-dlp Worker'})

# ── POST /extract ───────────────────────────────────────────────
# รับ URL → ดึง metadata + thumbnail + วิดีโอ (ขนาดเล็ก)
@app.route('/extract', methods=['POST'])
def extract():
    data = request.get_json()
    url  = (data or {}).get('url', '').strip()
    mode = (data or {}).get('mode', 'metadata')  # metadata | video | thumbnail

    if not url:
        return jsonify({'success': False, 'error': 'url required'}), 400

    try:
        if mode == 'metadata':
            return extract_metadata(url)
        elif mode == 'thumbnail':
            return extract_thumbnail(url)
        elif mode == 'video':
            return extract_video(url)
        else:
            return jsonify({'success': False, 'error': 'invalid mode'}), 400

    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


def run_ytdlp(args, timeout=60):
    """รัน yt-dlp command"""
    cmd = ['yt-dlp'] + args
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True, text=True,
        timeout=timeout,
        env={**os.environ, 'PYTHONUNBUFFERED': '1'}
    )
    return result


def extract_metadata(url):
    """ดึงแค่ metadata ไม่ดาวน์โหลดวิดีโอ"""
    result = run_ytdlp([
        '--dump-json',
        '--no-download',
        '--no-playlist',
        '--user-agent', 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
        url
    ], timeout=30)

    if result.returncode != 0:
        return jsonify({
            'success': False,
            'error': result.stderr[-500:] if result.stderr else 'yt-dlp failed'
        })

    try:
        info = json.loads(result.stdout)
        return jsonify({
            'success': True,
            'mode': 'metadata',
            'data': {
                'title':       info.get('title', ''),
                'description': info.get('description', ''),
                'uploader':    info.get('uploader', ''),
                'duration':    info.get('duration', 0),
                'thumbnail':   info.get('thumbnail', ''),
                'webpage_url': info.get('webpage_url', url),
                'tags':        info.get('tags', []),
                'like_count':  info.get('like_count', 0),
                'view_count':  info.get('view_count', 0),
            }
        })
    except json.JSONDecodeError as e:
        return jsonify({'success': False, 'error': f'JSON parse error: {str(e)}'})


def extract_thumbnail(url):
    """ดึง thumbnail เป็น base64"""
    with tempfile.TemporaryDirectory() as tmpdir:
        thumb_path = os.path.join(tmpdir, 'thumb')

        result = run_ytdlp([
            '--write-thumbnail',
            '--skip-download',
            '--no-playlist',
            '-o', thumb_path,
            '--user-agent', 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
            url
        ], timeout=30)

        # หาไฟล์ thumbnail
        thumb_file = None
        for ext in ['jpg', 'jpeg', 'png', 'webp']:
            candidate = f"{thumb_path}.{ext}"
            if os.path.exists(candidate):
                thumb_file = candidate
                break
        # หาในโฟลเดอร์
        if not thumb_file:
            for f in os.listdir(tmpdir):
                if any(f.endswith(e) for e in ['.jpg','.jpeg','.png','.webp']):
                    thumb_file = os.path.join(tmpdir, f)
                    break

        if not thumb_file:
            # fallback: ดึง metadata แทน
            return extract_metadata(url)

        with open(thumb_file, 'rb') as f:
            img_data = base64.b64encode(f.read()).decode()
        ext = thumb_file.split('.')[-1].lower()
        mime = 'image/jpeg' if ext in ['jpg','jpeg'] else f'image/{ext}'

        return jsonify({
            'success': True,
            'mode': 'thumbnail',
            'thumbnail_base64': img_data,
            'thumbnail_mime': mime,
        })


def extract_video(url):
    """ดาวน์โหลดวิดีโอขนาดเล็ก สำหรับส่งให้ Gemini วิเคราะห์"""
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, 'video.mp4')

        # ดึงวิดีโอขนาดเล็กที่สุด (ไม่เกิน 10MB สำหรับ Gemini)
        result = run_ytdlp([
            '-f', 'worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst[ext=mp4]/worst',
            '--merge-output-format', 'mp4',
            '--no-playlist',
            '-o', video_path,
            '--max-filesize', '15m',  # จำกัด 15MB
            '--user-agent', 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
            url
        ], timeout=120)

        if not os.path.exists(video_path):
            # ลองหาไฟล์อื่น
            found = None
            for f in os.listdir(tmpdir):
                if f.endswith(('.mp4', '.webm', '.mkv')):
                    found = os.path.join(tmpdir, f)
                    break
            if not found:
                return jsonify({
                    'success': False,
                    'error': f'Video download failed: {result.stderr[-300:] if result.stderr else "unknown"}'
                })
            video_path = found

        # ตรวจขนาดไฟล์
        size = os.path.getsize(video_path)
        print(f"Video size: {size/1024/1024:.1f} MB")

        if size > 20 * 1024 * 1024:  # > 20MB
            return jsonify({'success': False, 'error': f'Video too large: {size/1024/1024:.1f}MB (max 20MB)'})

        with open(video_path, 'rb') as f:
            video_data = base64.b64encode(f.read()).decode()

        # ดึง metadata ด้วย
        meta_result = run_ytdlp(['--dump-json', '--no-download', '--no-playlist', url], timeout=20)
        meta = {}
        if meta_result.returncode == 0:
            try:
                info = json.loads(meta_result.stdout)
                meta = {
                    'title': info.get('title', ''),
                    'description': info.get('description', ''),
                    'duration': info.get('duration', 0),
                }
            except: pass

        return jsonify({
            'success': True,
            'mode': 'video',
            'video_base64': video_data,
            'video_mime': 'video/mp4',
            'video_size_mb': round(size/1024/1024, 1),
            'metadata': meta,
        })


if __name__ == '__main__':
    print(f"yt-dlp Worker starting on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
