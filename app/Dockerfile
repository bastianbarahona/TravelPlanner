FROM python:3.12-slim

WORKDIR /app
COPY app/requirements.txt app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt

COPY app app
# The client validates provider responses against the contract itself, so the file ships in the image.
COPY contracts/openAPI.json contracts/openAPI.json

RUN useradd --system app
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
