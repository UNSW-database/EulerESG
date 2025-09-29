# ESG Analysis Backend System

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A robust backend system for ESG (Environmental, Social, and Governance) report analysis, providing intelligent data extraction, compliance assessment, and semantic search capabilities.

## 🚀 Features

- **Intelligent PDF Processing**: Automated extraction and structuring of ESG report content
- **SASB Standards Compliance**: Automated scoring against industry-specific SASB metrics
- **Dual-Channel Retrieval**: Hybrid search combining keyword matching and semantic similarity
- **AI-Powered Analysis**: Integration with LLM for intelligent disclosure assessment
- **Multi-Language Support**: Chinese and English language processing
- **RESTful API**: Well-documented API endpoints for seamless integration
- **Modular Architecture**: Clean, maintainable code structure with separated concerns

## 📦 Installation

### Prerequisites

- Python 3.10 or higher
- pip package manager
- Virtual environment (recommended)

### Quick Setup (Recommended)

1. **Clone the repository**
```bash
git clone https://github.com/yourusername/esg-backend.git
cd esg-backend
```

2. **Run the setup script**
```bash
python setup.py
```
This will:
- Create all required directories
- Set up configuration files
- Install dependencies (optional)
- Download language models (optional)

3. **Configure API Key**
```bash
# Edit the .env file
nano config/.env  # Or use any text editor

# Add your Qwen API key:
LLM_API_KEY=your-actual-api-key-here
```

### Manual Setup (Alternative)

1. **Clone and enter directory**
```bash
git clone https://github.com/yourusername/esg-backend.git
cd esg-backend
```

2. **Create virtual environment**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Create required directories**
```bash
mkdir -p uploads/reports/pending uploads/reports/processed
mkdir -p uploads/metrics uploads/outputs
mkdir -p outputs logs temp models
```

5. **Configure environment**
```bash
cp config/.env.example config/.env
# Edit config/.env with your API credentials
```

## 🔧 Configuration

### Environment Variables

Create a `.env` file in the `config/` directory:

```env
# LLM Configuration (Qwen-Plus)
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus

# API Server Configuration
API_HOST=0.0.0.0
API_PORT=8000
```

### Getting Qwen API Key

1. Visit [Alibaba Cloud DashScope](https://dashscope.console.aliyun.com/)
2. Register or login with your account
3. Navigate to "API Keys" section
4. Create a new API key
5. Copy the key and paste it in your `.env` file

### SASB Industry Support

The system supports the following SASB industry standards:

- Hardware
- Semiconductors
- Software & IT Services
- Internet Media & Services
- Telecommunication Services
- Electronic Manufacturing Services
- Commercial Banks
- Asset Management & Custody
- Investment Banking & Brokerage

## 🏃‍♂️ Running the Application

### Development Mode

```bash
# Navigate to src directory
cd src

# Run with uvicorn
uvicorn esg_encoding.api:app --reload --host 0.0.0.0 --port 8000
```

### Production Mode

```bash
# Using the start script
python scripts/start_backend.py

# Or with uvicorn directly
uvicorn esg_encoding.api:app --host 0.0.0.0 --port 8000 --workers 4
```

## 📚 API Documentation

Once the server is running, access the interactive API documentation:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### Main API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/upload_report` | POST | Upload ESG report PDF |
| `/upload_metrics` | POST | Upload SASB metrics file |
| `/process_report` | POST | Process uploaded report |
| `/compliance_assessment` | POST | Perform compliance assessment |
| `/chat` | POST | Interactive Q&A with report |
| `/system_status` | GET | Check system health |
| `/export_assessment` | POST | Export assessment to Excel |

## 🗂️ Project Structure

```
esg-backend/
├── src/
│   └── esg_encoding/
│       ├── api.py                    # FastAPI application
│       ├── report_encoder.py         # Document encoding
│       ├── metric_processor.py       # SASB metrics processing
│       ├── dual_channel_retrieval.py # Hybrid search
│       ├── disclosure_inference.py   # AI compliance analysis
│       ├── esg_chatbot.py           # Q&A functionality
│       ├── content_extractor.py     # PDF extraction
│       ├── content_embedder.py      # Semantic embeddings
│       ├── excel_exporter.py        # Export functionality
│       ├── file_manager.py          # File operations
│       ├── models.py                # Pydantic models
│       ├── exceptions.py            # Custom exceptions
│       └── utils.py                 # Utility functions
├── data/
│   └── sasb_metrics/                # SASB industry metrics
├── scripts/                         # Management scripts
├── config/                          # Configuration files
├── docs/                           # Documentation
└── requirements.txt                # Python dependencies
```

## 🔄 Core Modules

### 1. Content Extractor (`content_extractor.py`)
- PDF document parsing and text extraction
- Content cleaning and formatting
- Metadata extraction

### 2. Report Encoder (`report_encoder.py`)
- Document chunking and processing
- Vector embedding generation
- Semantic index construction

### 3. Metric Processor (`metric_processor.py`)
- SASB metrics parsing
- Excel/JSON data import
- Industry standard mapping

### 4. Dual Channel Retrieval (`dual_channel_retrieval.py`)
- Keyword exact matching
- Semantic similarity search
- Hybrid ranking algorithm

### 5. Disclosure Inference Engine (`disclosure_inference.py`)
- AI-driven compliance analysis
- Disclosure status assessment
- Automated compliance report generation

### 6. ESG Chatbot (`esg_chatbot.py`)
- Natural language understanding
- Context-aware dialogue management
- Multi-language support

## 🧪 Testing

Run the test suite:

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=esg_encoding tests/

# Run specific test file
pytest tests/test_api.py
```

## 📊 Performance

### Recommended System Requirements

- **CPU**: 4+ cores
- **RAM**: 8GB minimum, 16GB recommended
- **Storage**: 10GB for models and data
- **GPU**: Optional, improves embedding generation

## 🛠️ Development

### Code Style

Follow PEP 8 guidelines:

```bash
# Format code
black src/

# Check linting
flake8 src/
```

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 🐛 Troubleshooting

### Common Issues

**PDF extraction fails**
- Ensure PyMuPDF is properly installed
- Check PDF file permissions

**Model loading timeout**
- Pre-download sentence-transformers models
- Check internet connection for first-time model downloads

**API key errors**
- Verify .env configuration
- Check API key validity

## 📧 Support

For issues and questions, please open an issue on GitHub.

---

Built with ❤️ for sustainable business intelligence