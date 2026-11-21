// Where the website finds the backend (app/api.py, hosted on Render or Ninoo). The URL is not a secret:
// the Nicrron key stays on the server. Leave it empty to run with template drafts only.
// Example: backendUrl: "https://penncoin-backend.onrender.com"
window.FILINGS_CONFIG = Object.assign({ backendUrl: "" }, window.FILINGS_CONFIG || {});
