FROM python:3.14.3-alpine

WORKDIR /app

# Create config directory
RUN mkdir /config

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./

# Set Flask environment to production
ENV FLASK_ENV=production
ENV FLASK_DEBUG=0

# Expose port
EXPOSE 8085

# Run the application
CMD ["python", "app.py"]
