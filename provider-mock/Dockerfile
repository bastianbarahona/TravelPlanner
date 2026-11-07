FROM python:3.12-slim

WORKDIR /app
COPY provider-mock/requirements.txt provider-mock/requirements.txt
RUN pip install --no-cache-dir -r provider-mock/requirements.txt

COPY contracts/openAPI.json contracts/openAPI.json
COPY provider-mock/main.py provider-mock/fixtures.json provider-mock/

# The named volume inherits this ownership when Docker first creates it.
RUN useradd --system mock && mkdir /data && chown mock /data
USER mock

EXPOSE 4010
CMD ["uvicorn", "main:app", "--app-dir", "provider-mock", "--host", "0.0.0.0", "--port", "4010"]
