# syntax=docker/dockerfile:1
# The api Lambda image (spec §4, build step 5). Build context: api/ only.
#
#   docker buildx build --platform linux/arm64 -t dubainest-api api/
#   docker run --rm -p 8000:8000 -e AWS_REGION=us-east-1 dubainest-api
#
# Lambda Web Adapter, as in payroll-ai-strands: the managed Python runtime can't
# stream a response, so the adapter runs as a Lambda extension and forwards each
# invocation to this unchanged FastAPI app under uvicorn, streaming the reply
# back through the Function URL (AWS_LWA_INVOKE_MODE=response_stream, set by CDK).
# arm64 (Graviton): cheaper per ms on Lambda, native on Apple Silicon.
#
# agent/ is not in this image: on AWS the chat relay calls AgentCore (step 7).

ARG PYTHON_VERSION=3.12
ARG LWA_VERSION=0.9.1

FROM public.ecr.aws/docker/library/python:${PYTHON_VERSION}-slim AS build
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM public.ecr.aws/awsguru/aws-lambda-adapter:${LWA_VERSION} AS lwa

FROM public.ecr.aws/docker/library/python:${PYTHON_VERSION}-slim
COPY --from=lwa /lambda-adapter /opt/extensions/lambda-adapter
COPY --from=build /install /usr/local

WORKDIR /var/task
COPY . api/

RUN useradd --uid 10001 --no-create-home app
USER app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AWS_LWA_PORT=8000 \
    AWS_LWA_READINESS_CHECK_PATH=/health

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
