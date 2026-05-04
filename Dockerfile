FROM rayproject/ray:nightly-py311-cpu

WORKDIR /app

COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app

CMD ["bash", "-lc", "ray start --head --dashboard-host=0.0.0.0 && uvicorn main:app --host 0.0.0.0 --port 8000"]