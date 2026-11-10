# SPECTRA

Submission review workspace for reconciling application documents, extracted data, and account evidence. It includes a React dashboard, GPT-assisted review agents, deterministic checks, and citation previews.

## Repository layout

- `code/` — Python API, review pipeline, React UI, rules, and tests.
- `documents/` — account submission files and extracted JSON used by the review pipeline.

## Setup (Windows PowerShell)

Requires Python 3.11 or newer.

```powershell
cd code
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Add your `OPENAI_API_KEY` to `code\.env` to enable GPT-assisted reviews. Keep `.env` private; it is excluded from Git. Set `SPECTRA_LLM_ENABLED=true` to use GPT agents. Deterministic checks can run with LLM disabled.

## Start the dashboard

From the `code/` directory with the virtual environment active:

```powershell
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765/>.

## Run a review

From `code/`, for example:

```powershell
python -m app.cli.main review `
  --submission-id A02_Costco_Wholesale_Corporation `
  --input-dir ..\documents\A02_Costco_Wholesale_Corporation `
  --line-of-business package
```

Run the test suite from `code/` with `python -m pytest`.
