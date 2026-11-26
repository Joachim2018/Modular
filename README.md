# Modular

A responsive course-planning website with AI-generated learning resources, a per-module difficulty chart, printable plans, JSON export, and configurable job listings.

## Run locally

1. Install Python 3.10 or later (no Python packages are required).
2. Add your provider credentials to `.env`.
3. Start the server from this folder:

   ```powershell
   python server.py
   ```

4. Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The app validates course and module input in the browser and again on the server. API credentials are only read by the server; do not put secrets in `app.js` or `index.html`. Generated plans are saved to `data/generated.json` and can also be downloaded from the page. `.env` and generated output are excluded from Git.

## Provider setup

### OpenAI

Set `OPENAI_API_KEY`. The server uses the OpenAI Chat Completions API and the model in `OPENAI_MODEL` (defaults to `gpt-4o-mini`). Generation requires a valid API key and network access.

### JobsPipe

Set `JOBSPIPE_API_URL` to `https://api.jobspipe.dev` (or your JobsPipe API base URL) and `JOBSPIPE_API_KEY` to its bearer token. Modular sends a `POST` to `/v1/jobs/search` with the course as `job_title_or` and a limit of 10. It reads JobsPipe's `data` array and displays each job's title, company, location, and HTTPS posting URL.

### LinkedIn

LinkedIn job search is not generally available through an ordinary application client ID and client secret. Access depends on LinkedIn's approved products and permissions. The app includes the client ID/secret fields for your approved app configuration, but will not pretend those fields alone enable job results. To display API results, configure `LINKEDIN_ACCESS_TOKEN` and an authorized `LINKEDIN_OPPORTUNITIES_URL` that accepts a `q` query parameter and returns the same JSON shape as JobsPipe. Otherwise, the LinkedIn search button opens public LinkedIn job search for the entered course. Never expose the client secret or access token to the browser.

## UI libraries and assets

Bootstrap, Animate.css, Bootstrap Icons, and Chart.js are loaded from their CDNs. The supplied logo is in the project root as `modularLogo.png`; the homepage includes Pexels photography, and the YouTube embed uses YouTube's privacy-enhanced host.
