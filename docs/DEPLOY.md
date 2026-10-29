# Deploying BenefitScout

The app is a single container: FastAPI serves the API and the static front end. Any Docker host works.
Judges need a public URL, so pick one of these.

## Render (free, no card) — what the demo runs on

`render.yaml` at the repo root is a Render blueprint. One click:

1. Sign in at https://render.com with GitHub.
2. Open https://render.com/deploy?repo=https://github.com/rupakkumar-76319/benefitscout
   (or New > Blueprint and pick the repository).
3. Render asks for the two secrets it cannot read from the file: `NEBIUS_API_KEY` and `TAVILY_API_KEY`. Paste them, then Apply.
4. The first build takes 3-5 minutes. The service URL is `https://benefitscout.onrender.com` (or with a suffix if the name is taken).

The free plan sleeps after 15 minutes without traffic and takes about a minute to wake, so open the URL once before a demo.
Saved runs live in `/data` inside the container and do not survive a redeploy, which is fine for a demo.

## Hugging Face Space

Docker Spaces need a paid plan since late 2026 (only Static Spaces are free). If you have PRO: create a Docker Space,
add the two secrets in Settings, and push the `space` branch of this repo, whose README starts with the Space front matter:

```
---
title: BenefitScout
emoji: "✅"
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
---
```

```bash
git remote add space https://huggingface.co/spaces/<user>/benefitscout
git push space space:main
```

## Nebius (encouraged by the track, not required)

Build and push the image to any registry, then create a Serverless Endpoint from it with the two secrets as environment
variables and port 7860. Run `python scripts/recheck.py` as a Serverless Job on a schedule for the watcher.

## Anywhere with Docker

```bash
docker build -t benefitscout .
docker run -d -p 7860:7860 --env-file .env -v benefitscout-data:/data benefitscout
```

## Local

```bash
uvicorn benefitscout.server:app --port 8000
```
