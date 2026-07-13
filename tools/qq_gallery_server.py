#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


HOST = os.getenv('QQMD_UPLOAD_HOST', '127.0.0.1')
PORT = int(os.getenv('QQMD_UPLOAD_PORT', '20259'))
UPLOAD_TOKEN = os.getenv('QQMD_UPLOAD_TOKEN', '')
PUBLIC_BASE_URL = os.getenv('QQMD_PUBLIC_BASE_URL', '').rstrip('/')
GALLERY_DIR = Path(os.getenv('QQMD_GALLERY_DIR', '/root/qqmd'))
MAX_IMAGE_BYTES = int(os.getenv('QQMD_MAX_IMAGE_BYTES', str(12 * 1024 * 1024)))


def image_extension(data: bytes) -> str | None:
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'gif'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    return None


class UploadHandler(BaseHTTPRequestHandler):
    server_version = 'TodayWaifuGallery/1.0'

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if urlparse(self.path).path == '/healthz':
            self.send_json(200, {'status': 'ok'})
            return
        self.send_json(404, {'error': 'not_found'})

    def do_POST(self) -> None:
        if urlparse(self.path).path != '/upload':
            self.send_json(404, {'error': 'not_found'})
            return
        if not UPLOAD_TOKEN:
            self.send_json(503, {'error': 'upload_token_not_configured'})
            return

        authorization = self.headers.get('Authorization', '')
        expected = f'Bearer {UPLOAD_TOKEN}'
        if not hmac.compare_digest(authorization, expected):
            self.send_json(401, {'error': 'unauthorized'})
            return

        content_length = self.headers.get('Content-Length', '')
        if not content_length.isdigit():
            self.send_json(411, {'error': 'content_length_required'})
            return
        size = int(content_length)
        if size <= 0 or size > MAX_IMAGE_BYTES:
            self.send_json(413, {'error': 'image_too_large'})
            return

        data = self.rfile.read(size)
        if len(data) != size:
            self.send_json(400, {'error': 'incomplete_body'})
            return
        extension = image_extension(data)
        if extension is None:
            self.send_json(415, {'error': 'unsupported_image'})
            return

        digest = hashlib.sha256(data).hexdigest()
        file_name = f'todaywaifu-{digest[:32]}.{extension}'
        GALLERY_DIR.mkdir(parents=True, exist_ok=True)
        target = GALLERY_DIR / file_name
        if not target.exists():
            temporary = GALLERY_DIR / f'.{file_name}.tmp-{os.getpid()}'
            temporary.write_bytes(data)
            os.replace(temporary, target)
            target.chmod(0o644)

        self.send_json(200, {'url': f'{PUBLIC_BASE_URL}/{file_name}'})

    def log_message(self, format: str, *args: object) -> None:
        print(f'{self.address_string()} - {format % args}', flush=True)


def main() -> None:
    if not PUBLIC_BASE_URL:
        raise SystemExit('QQMD_PUBLIC_BASE_URL is required')
    if not UPLOAD_TOKEN:
        raise SystemExit('QQMD_UPLOAD_TOKEN is required')
    ThreadingHTTPServer((HOST, PORT), UploadHandler).serve_forever()


if __name__ == '__main__':
    main()
