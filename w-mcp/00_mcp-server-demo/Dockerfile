FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "mcp>=1.12,<2" uvicorn
COPY w-mcp_demo_server.py ./mcp_server.py
COPY asgi_both.py ./
ENV DEMO_MCP_HOST=0.0.0.0 \
    DEMO_MCP_PORT=16684 \
    MCP_MODULE=mcp_server
EXPOSE 16684
CMD ["sh", "-c", "uvicorn asgi_both:app --host 0.0.0.0 --port ${DEMO_MCP_PORT}"]
