import asyncio
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

app = FastAPI()

@app.middleware("http")
async def test_mw(request: Request, call_next):
    response = await call_next(request)
    print("Path:", request.url.path)
    print("Headers:", response.headers)
    if "text/html" in response.headers.get("content-type", ""):
        response.headers["content-type"] = "text/html"
    return response

app.mount("/", StaticFiles(directory="/Books"), name="books")
