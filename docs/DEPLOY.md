# Deploying BenefitScout

The app is a single container: FastAPI serves the API and the static front end. Any Docker host works.
Judges need a public URL, so pick one of these.

## Hugging Face Space (free CPU, no card)

1. Create a Space: type **Docker**, visibility public, hardware CPU basic.
2. In the Space settings add two **secrets**: `NEBIUS_API_KEY`, `TAVILY_API_KEY`
   (and optionally `BS_DATA_DIR=/data` as a variable; the Dockerfile already sets it).
3. Push this repository to the Space, with a README that starts with the Space front matter:

```
---
title: BenefitScout
emoji: ✅
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
---
```

   The GitHub README does not need the front matter, so keep a separate branch or prepend it in the Space copy:

```bash
git remote add space https://huggingface.co/spaces/<user>/benefitscout
git checkout -b space
{ printf -- '---\ntitle: BenefitScout\nemoji: ✅\ncolorFrom: green\ncolorTo: gray\nsdk: docker\napp_port: 7860\npinned: false\nlicense: mit\n---\n\n'; cat README.md; } > README.tmp && mv README.tmp README.md
git commit -am "Space front matter" && git push space space:main
git checkout main
```

Saved runs live in `/data` inside the container, which does not survive a rebuild on the free tier; that is fine for a demo.

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
