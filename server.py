from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT_FILE = ROOT / "data" / "generated.json"
MAX_BODY_BYTES = 16_384
TEXT_PATTERN = re.compile(r"^[\w\s&.,()/'’:+#-]+$", re.UNICODE)
STATIC_FILES = {"index.html", "styles.css", "app.js", "api-config.js", "modularLogo.png"}
DEFAULT_CORS_ORIGINS = {
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://joachim2018.github.io",
}


def allowed_cors_origins() -> set[str]:
    configured = os.environ.get("CORS_ALLOWED_ORIGINS")
    origins = configured.split(",") if configured else DEFAULT_CORS_ORIGINS
    return {origin.strip().rstrip("/") for origin in origins if origin.strip()}


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def validate_text(value: object, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > max_length or not TEXT_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} must be 1–{max_length} characters and use letters, numbers, and common punctuation.")
    return normalized


def validate_request(payload: object) -> tuple[str, list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    course = validate_text(payload.get("course"), "Course", 100)
    raw_modules = payload.get("modules")
    if not isinstance(raw_modules, list) or not 2 <= len(raw_modules) <= 12:
        raise ValueError("Provide between 2 and 12 modules.")
    modules = [validate_text(module, "Each module", 80) for module in raw_modules]
    if len({module.casefold() for module in modules}) != len(modules):
        raise ValueError("Module names must be unique.")
    return course, modules


def call_json_api(url: str, payload: dict, headers: dict[str, str], timeout: int = 35) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"Provider returned HTTP {error.code}: {detail[:500]}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Unable to reach the configured provider: {error}") from error
    if not isinstance(result, dict):
        raise RuntimeError("The configured provider returned an unexpected response.")
    return result


def extract_openai_content(response: dict) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("OpenAI returned no completion choices.")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    raise RuntimeError("OpenAI returned an empty learning plan.")


def clean_plan(raw_plan: object, course: str, requested_modules: list[str]) -> dict:
    if not isinstance(raw_plan, dict) or not isinstance(raw_plan.get("modules"), list):
        raise RuntimeError("The AI response did not contain a valid modules list.")
    by_name = {}
    for item in raw_plan["modules"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        by_name[item["name"].strip().casefold()] = item

    modules = []
    for requested in requested_modules:
        item = by_name.get(requested.casefold())
        if not item:
            raise RuntimeError(f"The AI response did not include the requested module: {requested}. Please try again.")
        resources = []
        raw_resources = item.get("resources", [])
        if isinstance(raw_resources, list):
            for resource in raw_resources[:5]:
                if not isinstance(resource, dict):
                    continue
                title = resource.get("title")
                url = resource.get("url")
                if not isinstance(title, str) or not isinstance(url, str):
                    continue
                parsed_url = urllib.parse.urlparse(url)
                if parsed_url.scheme != "https" or not parsed_url.netloc:
                    continue
                resources.append({
                    "title": title[:140],
                    "type": str(resource.get("type", "Resource"))[:40],
                    "url": url[:1000],
                })
        if not resources:
            raise RuntimeError(f"No usable HTTPS learning resources were returned for {requested}. Please try again.")
        modules.append({
            "name": requested,
            "complexity": clamp_score(item.get("complexity")),
            "difficulty": clamp_score(item.get("difficulty")),
            "summary": str(item.get("summary", "Explore the key ideas and practice applying them."))[:500],
            "resources": resources,
        })
    return {
        "course": course,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "modules": modules,
    }


def clamp_score(value: object) -> int:
    if isinstance(value, bool):
        return 5
    try:
        return max(1, min(10, int(round(float(value)))))
    except (TypeError, ValueError, OverflowError):
        return 5


def generate_plan(course: str, modules: list[str]) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OpenAI is not configured. Add OPENAI_API_KEY to your .env file and restart the server.")
    prompt = (
        "Create a practical study guide for the provided course and modules. Return ONLY a JSON object with "
        'a "modules" array. Include exactly one object for each requested module, keeping the module name exact. '
        'Each module object has: "name" (string), "complexity" (integer 1-10, conceptual breadth), '
        '"difficulty" (integer 1-10, learner challenge), "summary" (short plain text), and '
        '"resources" (2-4 objects with "title", "type" [Video, Website, or Tool], and "url"). '
        "Only provide plausible, direct HTTPS learning resources, such as established educational sites or YouTube search/video links; "
        "never invent claims that a link was checked. Keep results suitable for students."
    )
    response = call_json_api(
    "https://api.openai.com/v1/chat/completions",
    {
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {"course": course, "modules": modules},
                    ensure_ascii=False
                )
            },
        ],
        "temperature": 0.4,
    },
    {"Authorization": f"Bearer {api_key}"},
)
    content = extract_openai_content(response)
    try:
        raw_plan = json.loads(content)
    except json.JSONDecodeError as error:
        raise RuntimeError("OpenAI returned a plan that could not be read as JSON. Please try again.") from error
    return clean_plan(raw_plan, course, modules)


def normalize_jobs(result: object) -> list[dict]:
    if isinstance(result, list):
        rows = result
    elif isinstance(result, dict):
        rows = result.get("jobs", result.get("results", result.get("data", [])))
    else:
        rows = []
    if not isinstance(rows, list):
        return []
    jobs = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        title = row.get("title") or row.get("job_title") or row.get("name")
        url = row.get("url") or row.get("job_url") or row.get("apply_url") or row.get("link") or row.get("source_url")
        if not isinstance(title, str) or not isinstance(url, str):
            continue
        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            continue
        jobs.append({
            "title": title[:180],
            "company": str(row.get("company") or row.get("company_name") or "")[:120],
            "location": str(row.get("location") or "")[:120],
            "url": url[:1000],
        })
    return jobs


class ModularHandler(BaseHTTPRequestHandler):
    server_version = "Modular/1.0"

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"{self.address_string()} - {format_string % args}")

    def send_cors_headers(self) -> None:
        origin = self.headers.get("Origin", "").rstrip("/")
        if origin and origin in allowed_cors_origins():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        if not urllib.parse.urlparse(self.path).path.startswith("/api/"):
            self.send_json(404, {"error": "Route not found."})
            return
        origin = self.headers.get("Origin", "").rstrip("/")
        if origin and origin not in allowed_cors_origins():
            self.send_json(403, {"error": "Origin is not allowed."})
            return
        self.send_response(204)
        self.send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_json(self) -> object:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid request body size.") from error
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("Request body is empty or too large.")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Request body must be valid JSON.") from error

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/status":
            self.send_json(200, {
                "openai": bool(os.environ.get("OPENAI_API_KEY")),
                "jobsPipe": bool(os.environ.get("JOBSPIPE_API_URL") and os.environ.get("JOBSPIPE_API_KEY")),
                "linkedin": bool(os.environ.get("LINKEDIN_OPPORTUNITIES_URL") and os.environ.get("LINKEDIN_ACCESS_TOKEN")),
            })
            return
        if parsed.path == "/api/output":
            if self.client_address[0] not in {"127.0.0.1", "::1"}:
                self.send_json(404, {"error": "No saved learning plan."})
                return
            if not OUTPUT_FILE.is_file():
                self.send_json(404, {"error": "No saved learning plan yet."})
                return
            try:
                output = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                self.send_json(500, {"error": f"Could not read the saved plan: {error}"})
                return
            self.send_json(200, output)
            return
        if parsed.path == "/api/jobs":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                course = validate_text(query.get("course", [""])[0], "Course", 100)
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
                return
            try:
                jobs = self.fetch_opportunities(course)
            except RuntimeError as error:
                self.send_json(503, {"error": str(error)})
                return
            self.send_json(200, {"jobs": jobs})
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/generate":
            self.send_json(404, {"error": "Route not found."})
            return
        try:
            course, modules = validate_request(self.read_json())
            plan = generate_plan(course, modules)
            self.write_plan(plan)
            self.send_json(200, plan)
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except RuntimeError as error:
            self.send_json(502, {"error": str(error)})
        except OSError as error:
            self.send_json(500, {"error": f"Could not save generated output: {error}"})

    def write_plan(self, plan: dict) -> None:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = OUTPUT_FILE.with_suffix(".tmp")
        temporary_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary_file.replace(OUTPUT_FILE)

    def fetch_opportunities(self, course: str) -> list[dict]:
        jobs_url = os.environ.get("JOBSPIPE_API_URL", "").strip()
        jobs_key = os.environ.get("JOBSPIPE_API_KEY", "").strip()
        if jobs_url and jobs_key:
            result = self.request_jobspipe(jobs_url, jobs_key, course)
            return normalize_jobs(result)

        linkedin_url = os.environ.get("LINKEDIN_OPPORTUNITIES_URL", "").strip()
        linkedin_token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "").strip()
        if linkedin_url and linkedin_token:
            result = self.request_opportunity_provider(linkedin_url, linkedin_token, course)
            return normalize_jobs(result)

        raise RuntimeError(
            "No job API is configured. Add JobsPipe URL/key or an authorized LinkedIn opportunities URL/access token to .env."
        )

    @staticmethod
    def request_jobspipe(endpoint: str, token: str, course: str) -> object:
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError("JobsPipe API URLs must use HTTPS.")
        if parsed.path in {"", "/"}:
            parsed = parsed._replace(path="/v1/jobs/search", query="", fragment="")
        request = urllib.request.Request(
            urllib.parse.urlunparse(parsed),
            data=json.dumps({"job_title_or": [course], "limit": 10}).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode("utf-8", errors="replace")
            raise RuntimeError(f"JobsPipe returned HTTP {error.code}: {detail[:400]}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Unable to reach JobsPipe: {error}") from error

    @staticmethod
    def request_opportunity_provider(endpoint: str, token: str, course: str) -> object:
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError("Configured job API URLs must use HTTPS.")
        separator = "&" if parsed.query else "?"
        url = f"{endpoint}{separator}{urllib.parse.urlencode({'q': course})}"
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode("utf-8", errors="replace")
            raise RuntimeError(f"Job provider returned HTTP {error.code}: {detail[:400]}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Unable to reach the configured job provider: {error}") from error

    def serve_static(self, requested_path: str) -> None:
        relative = urllib.parse.unquote(requested_path).lstrip("/")
        if not relative:
            relative = "index.html"
        target = (ROOT / relative).resolve()
        if relative not in STATIC_FILES or not target.is_relative_to(ROOT) or not target.is_file():
            self.send_error(404)
            return
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
        }
        try:
            body = target.read_bytes()
        except OSError:
            self.send_error(500)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(target.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    load_env()
    host = "0.0.0.0" if os.environ.get("RENDER") == "true" else os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), ModularHandler)
    print(f"Modular is running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Modular.")
    finally:
        server.server_close()
