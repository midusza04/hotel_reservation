FROM rayproject/ray:nightly-py311-cpu

WORKDIR /app

COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app

# ---- head node (default) ----
# Starts Ray head and then runs FastAPI on top.
CMD ["bash", "-lc", "ray start --head --dashboard-host=0.0.0.0 --port=6379 --num-cpus=1 && uvicorn main:app --host 0.0.0.0 --port 8000"]