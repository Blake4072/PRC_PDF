FROM python:3.10-slim

# Install OS-level dependencies required for pyodbc and SQL Server access
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    unixodbc \
    unixodbc-dev \
 && rm -rf /var/lib/apt/lists/*

# Install Microsoft ODBC Driver 18
RUN curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
 | gpg --dearmor \
 | tee /etc/apt/trusted.gpg.d/microsoft.gpg > /dev/null && \
 curl -sSL https://packages.microsoft.com/config/debian/11/prod.list \
 > /etc/apt/sources.list.d/mssql-release.list && \
 apt-get update && \
 ACCEPT_EULA=Y apt-get install -y msodbcsql18 && \
 rm -rf /var/lib/apt/lists/*

# Set working directory inside the container
WORKDIR /app

# Ensure predictable Python runtime behavior
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Tell the app where to write generated PDFs
ENV PRC_OUTPUT_PDF_DIR=/app/output_pdfs

# Create directories that will be mounted as persistent storage in production
RUN mkdir -p /app/output_pdfs /app/data

# Install Python dependencies required by the app
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copy the application source code into the container
COPY . .

# Document the port the app listens on
EXPOSE 8000

# Start the production web server
CMD ["python", "run_waitress.py"]