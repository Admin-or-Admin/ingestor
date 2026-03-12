# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY ingestor/requirements.txt ingestor/

# Copy the shared directory into the container at /app
COPY shared/ shared/

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r ingestor/requirements.txt

# Copy the current directory contents into the container at /app
COPY ingestor/ ingestor/

# Set the working directory to the service folder
WORKDIR /app/ingestor

# Run main.py when the container launches
CMD ["python", "main.py"]
