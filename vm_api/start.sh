#!/bin/bash
echo "Starting InterSync VM API Server on 0.0.0.0:5000..."
uvicorn server:app --host 0.0.0.0 --port 5000
