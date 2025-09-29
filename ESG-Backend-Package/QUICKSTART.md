# 🚀 Quick Start Guide

## Step 1: Initial Setup (5 minutes)

```bash
# 1. Clone the repository
git clone https://github.com/yourusername/esg-backend.git
cd esg-backend

# 2. Run setup script
python setup.py
# Choose 'y' to install dependencies
# Choose 'y' to download models (optional, but recommended)
```

## Step 2: Configure API Key (1 minute)

Open `config/.env` in any text editor and replace the placeholder:

```env
LLM_API_KEY=sk-your-actual-api-key-here
```

To get a Qwen API key:
1. Visit https://dashscope.console.aliyun.com/
2. Register/login
3. Create an API key
4. Copy and paste it into `.env`

## Step 3: Start the Server (1 minute)

```bash
# Run the backend
python scripts/start_backend.py
```

Wait for the message: `Uvicorn running on http://0.0.0.0:8000`

## Step 4: Verify Installation

Open your browser and visit:
- API Documentation: http://localhost:8000/docs
- Health Check: http://localhost:8000/system_status

## 🎉 You're Ready!

### Test the API

1. Go to http://localhost:8000/docs
2. Click on any endpoint to expand it
3. Click "Try it out"
4. Execute the request

### Upload Your First Report

Using the API docs interface:
1. Find `/upload_report` endpoint
2. Click "Try it out"
3. Choose a PDF file
4. Set company name and industry
5. Click "Execute"

### Common Issues

**Port Already in Use:**
```bash
# Change port in scripts/start_backend.py
# Or kill the process using port 8000
```

**Import Errors:**
```bash
# Reinstall dependencies
pip install -r requirements.txt --upgrade
```

**API Key Invalid:**
- Double-check your API key in `config/.env`
- Ensure no extra spaces or quotes

## Next Steps

- Read the full [README.md](README.md) for detailed documentation
- Explore the [API Documentation](http://localhost:8000/docs)
- Check example SASB metrics in `data/sasb_metrics/`

## Support

If you encounter issues:
1. Check the logs in `logs/` directory
2. Open an issue on GitHub
3. Include error messages and system info