"""Local-only web server; rebuild the analysis on every launch."""
import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from pipeline import run

ROOT = Path(__file__).resolve().parent


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, output_dir, **kwargs):
        self.output_dir = output_dir
        super().__init__(*args, **kwargs)

    def do_GET(self):
        routes = {'/': (ROOT / 'web/index.html', 'text/html; charset=utf-8'),
                  '/app.js': (ROOT / 'web/app.js', 'text/javascript; charset=utf-8'),
                  '/investigation.js': (ROOT / 'web/investigation.js', 'text/javascript; charset=utf-8'),
                  '/style.css': (ROOT / 'web/style.css', 'text/css; charset=utf-8'),
                  '/api/graph': (self.output_dir / 'graph.json', 'application/json; charset=utf-8')}
        for name in ('nodes_roles', 'clusters', 'top_nodes'):
            routes[f'/download/{name}.csv'] = (self.output_dir / f'{name}.csv', 'text/csv; charset=utf-8')
        for font in (ROOT / 'web/fonts').glob('*.woff2'):
            routes[f'/fonts/{font.name}'] = (font, 'font/woff2')
        path = urlparse(self.path).path
        if path not in routes:
            self.send_error(404)
            return
        file, content_type = routes[path]
        data = file.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if path.startswith('/download/'):
            self.send_header('Content-Disposition', f'attachment; filename="{file.name}"')
        self.end_headers()
        self.wfile.write(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=ROOT / 'data')
    parser.add_argument('--out', type=Path, default=ROOT / 'output')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    run(args.data, args.out)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(Handler, output_dir=args.out.resolve()))
    print(f'Граф денег: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
