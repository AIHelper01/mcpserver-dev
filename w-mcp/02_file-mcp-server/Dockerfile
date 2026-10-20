FROM python:3.12-slim
WORKDIR /opt/app
RUN pip install --no-cache-dir "mcp>=1.12,<2" python-dotenv uvicorn
COPY w-file_workbench_mcp_server.py /opt/app/mcp_server.py
COPY asgi_both.py /opt/app/
ENV PYTHONUNBUFFERED=1 \
    FILE_MCP_HOST=0.0.0.0 \
    FILE_MCP_PORT=16682 \
    MCP_MODULE=mcp_server
EXPOSE 16682
CMD ["sh", "-c", "cd /app && PYTHONPATH=/opt/app uvicorn asgi_both:app --host 0.0.0.0 --port ${FILE_MCP_PORT}"]
