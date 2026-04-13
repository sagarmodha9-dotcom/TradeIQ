"""
web_server.py — Secure web server that only serves tradeiq_app.html
Nothing else in the directory is accessible.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Only serve the dashboard HTML — nothing else
        if self.path in ["/", "/tradeiq_app.html"]:
            try:
                with open("tradeiq_app.html", "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", len(content))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress logs

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8080), Handler)
    print("Web server running on port 8080")
    server.serve_forever()
