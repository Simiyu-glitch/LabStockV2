# main.py — LabStockV2 FastAPI Entry Point

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import auth
import charts
import communications
import tasks_api
import rota_api
import leave_api

app = FastAPI(title="LabStockV2 API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(charts.router)
app.include_router(communications.router)
app.include_router(tasks_api.router)
app.include_router(rota_api.router)
app.include_router(leave_api.router)

@app.get("/")
def root():
    return {"status": "LabStockV2 backend running"}
