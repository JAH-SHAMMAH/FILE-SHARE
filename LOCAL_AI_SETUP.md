# Local AI Setup (CPU‑only)

This app can use **local AI** through Ollama. Your PC is CPU‑only, so use a small model.

## 1) Install Ollama (Windows)
- Download and install: https://ollama.com/download
- After install, confirm it runs by opening a new terminal and running:
  - `ollama --version`

## 2) Pull a small model
Recommended for your PC:
- `ollama pull llama3.2:3b`

## 3) Configure the app to use local AI
Create or update your `.env` file:

```
AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
```

## 4) Run Ollama
Start Ollama (it runs as a service). You can verify:
- `ollama list`

## 5) Run your app
Restart your app server. The AI routes will now use local AI.

## Notes
- CPU‑only is slower. Expect a few seconds per request.
- For more speed, use a smaller model or add a GPU.
