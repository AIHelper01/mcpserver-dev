FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "mcp>=1.12,<2" python-dotenv requests uvicorn
COPY w-openweather_mcp_server.py ./mcp_server.py
COPY asgi_both.py ./
ENV WEATHER_MCP_HOST=0.0.0.0 \
    WEATHER_MCP_PORT=16681 \
    MCP_MODULE=mcp_server
EXPOSE 16681
CMD ["sh", "-c", "uvicorn asgi_both:app --host 0.0.0.0 --port ${WEATHER_MCP_PORT}"]
