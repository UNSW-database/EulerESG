🌱 ESG Report Analysis System

A full-stack ESG (Environmental, Social, Governance) report analysis platform that provides intelligent ESG data extraction, compliance analysis, and interactive querying.

✨ System Features
	•	📄 Intelligent PDF Parsing – Automatically extracts ESG report content and structures it
	•	🔍 Dual-Channel Retrieval – Hybrid search combining keyword matching and semantic retrieval
	•	📊 SASB Standards Evaluation – Automatic compliance scoring based on industry standards
	•	💬 Intelligent Q&A – Interactive ESG data querying in both Chinese and English
	•	📈 Visual Analytics – Intuitive visualization of analysis results and compliance levels

📁 Project Structure

ESG DEMO/
├── backend/                    # Python backend service
│   ├── src/                   # Core source code
│   │   └── esg_encoding/      # ESG processing modules (13 core modules)
│   ├── scripts/               # Management and startup scripts
│   ├── data/sasb_metrics/     # SASB industry metrics
│   ├── outputs/               # Generated compliance reports
│   ├── docs/                  # Backend documentation
│   └── config/                # Environment configuration files
├── ESG-demo-main/             
│   └── frontend/              # Next.js frontend application
│       ├── src/               
│       │   ├── app/           # Next.js 15 App Router
│       │   └── components/    # React component library
│       ├── public/            # Static assets
│       └── out/               # Build output
├── uploads/                   # File storage system
│   ├── reports/               # ESG report storage
│   │   ├── pending/           # Reports pending processing
│   │   └── processed/         # Processed reports
│   ├── metrics/               # Metrics files
│   └── outputs/              # Processing results
├── data/                      # Sample data files
├── logs/                      # System logs
├── docs/                      # Project documentation
└── scripts/                   # Project-level scripts

🚀 Quick Start

Requirements
	•	Python 3.10+
	•	Node.js 16+
	•	npm or yarn

Recommended: Start Backend & Frontend Separately

Important: Due to encoding issues on Windows, it is recommended to start the backend and frontend separately for better stability.

Step 1: Start the Backend Service

cd backend
python scripts/start_backend.py

Wait until the backend is fully started (you should see both “Application startup complete” and “Uvicorn running” messages).

Step 2: Start the Frontend App

# Open a new terminal window
cd ESG-demo-main/frontend
npm install  # Install dependencies on first run
npm run dev -- --port 3001

One-Click Start (Optional)

# Start the full system (frontend + backend)
# Note: May encounter encoding issues on some Windows systems
python scripts/start_project.py

Manual Startup

Backend Service (manual mode)

cd backend/src
uvicorn esg_encoding.api:app --host 0.0.0.0 --port 8000

Frontend App (manual mode)

cd ESG-demo-main/frontend
npm install  # Install dependencies on first run
npm run dev -- --port 3001

🔗 Access URLs
	•	Frontend UI: http://localhost:3001
	•	Backend API: http://localhost:8000
	•	API Docs: http://localhost:8000/docs

Note: The frontend runs on port 3001, and the backend API runs on port 8000.

💻 Tech Stack

Backend
	•	Framework: FastAPI (high-performance async framework)
	•	AI/ML:
	•	Sentence Transformers (semantic embeddings)
	•	Tongyi Qianwen API (Chinese LLM)
	•	PyTorch (deep learning)
	•	Data Processing:
	•	PyPDF2 (PDF parsing)
	•	Pandas (data analysis)
	•	NumPy (numerical computing)

Frontend
	•	Framework: Next.js 15.3.3 (App Router)
	•	UI Libraries:
	•	Ant Design 5.25
	•	Tailwind CSS 4
	•	Radix UI
	•	State Management: Zustand 5
	•	PDF Rendering: React-PDF 7.7

🔧 Core Functional Modules

1. Content Extractor (content_extractor.py)
	•	PDF document parsing and text extraction
	•	Content cleaning and formatting
	•	Metadata extraction

2. Report Encoder (report_encoder.py)
	•	Document chunking
	•	Vector embedding generation
	•	Semantic index construction

3. Metric Processor (metric_processor.py)
	•	SASB metrics parsing
	•	Excel/JSON data import
	•	Mapping to industry standards

4. Dual-Channel Retriever (dual_channel_retrieval.py)
	•	Exact keyword matching
	•	Semantic similarity search
	•	Hybrid ranking algorithm

5. Disclosure Inference Engine (disclosure_inference.py)
	•	AI-powered compliance analysis
	•	Disclosure status assessment
	•	Automatic compliance report generation

6. ESG Chatbot (esg_chatbot.py)
	•	Natural language understanding
	•	Contextual dialogue management
	•	Multi-language support

🛠️ Configuration

Environment Variables
	1.	Copy the environment template:

cp backend/config/.env.example backend/config/.env

	2.	Edit the .env file to configure API keys:

# LLM configuration (Tongyi Qianwen)
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://dashscope.aliyuncs.com/api/v1
LLM_MODEL=qwen-plus

SASB Industry Coverage

The system currently supports the following SASB industry standards:
	•	Electronic Manufacturing Services
	•	Hardware
	•	Internet Media & Services
	•	Semiconductors
	•	Software & IT Services
	•	Telecommunications Services

📝 Usage Workflow
	1.	Upload Report – Upload an ESG PDF report to the system
	2.	Select Industry – Choose the corresponding SASB industry category
	3.	Automatic Processing – The system automatically extracts and analyzes content
	4.	View Results – Check the compliance assessment report
	5.	Intelligent Q&A – Use the chat interface to explore report details

🗂️ Data Flow

Upload → uploads/reports/pending/
  ↓
Processing → uploads/reports/processed/
  ↓
Analysis → backend/outputs/
  ↓
Display → Frontend UI

📊 API Endpoints

Main API endpoints:
	•	POST /upload_report – Upload an ESG report
	•	POST /upload_metrics – Upload a metrics file
	•	POST /process_report – Process a report
	•	POST /compliance_assessment – Compliance assessment
	•	POST /chat – Intelligent Q&A
	•	GET /system_status – System status

For detailed API documentation, visit:
http://localhost:8000/docs

🔍 Monitoring & Maintenance

System Health Check

python backend/scripts/system_health_check.py

Backend Monitoring

python backend/monitor_backend.py

Log Locations
	•	API logs: logs/esg_api_server.log
	•	System logs: backend/logs/

🤝 Contributing
	1.	Fork the repository
	2.	Create a feature branch (git checkout -b feature/AmazingFeature)
	3.	Commit your changes (git commit -m 'Add some AmazingFeature')
	4.	Push to the branch (git push origin feature/AmazingFeature)
	5.	Open a Pull Request

📄 License

This project is licensed under the MIT License – see the LICENSE￼ file for details.

🆘 Troubleshooting

Startup Issues
	1.	Encoding Error in One-Click Start Script

Error: UnicodeEncodeError: 'gbk' codec can't encode character

Solution: Use the separate startup method instead of the one-click script to avoid Windows encoding issues.

	2.	Port Already in Use

Error: [Errno 10048] error while attempting to bind on address

Steps to fix:

# 1. Check which process is using the port
netstat -ano | findstr :8000

# 2. Kill the process (replace <PID> with the actual process ID)
powershell -Command "Stop-Process -Id <PID> -Force"

# 3. Restart the backend
cd backend && python scripts/start_backend.py


	3.	Backend Started but Not Reachable
	•	Make sure you see the message “Application startup complete”
	•	Wait for the model to finish loading (about 20–30 seconds)
	•	Verify the API docs at: http://localhost:8000/docs
	4.	Frontend Fails to Start

# Check Node.js version (must be 16+)
node --version

# Reinstall dependencies
cd ESG-demo-main/frontend
rm -rf node_modules package-lock.json
npm install

# Start the frontend
npm run dev -- --port 3001



Common Issues
	1.	Port Configuration
	•	Frontend default port: 3001
	•	Backend default port: 8000
	•	All port configurations have been unified and updated.
	2.	Dependency Installation Failure

# Python dependencies
pip install -r backend/requirements.txt

# Node dependencies
cd ESG-demo-main/frontend && npm install


	3.	API Key Configuration
	•	Ensure backend/config/.env exists
	•	Check that the API key is correctly set

Indicators of Successful Startup

Backend successfully started if you see:

INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)

Frontend successfully started if you see:

▲ Next.js 15.3.3
- Local:        http://localhost:3001
- Network:      http://192.168.x.x:3001

✓ Ready in 1275ms

System fully ready when:
	•	Visiting http://localhost:3001 shows the frontend UI
	•	Visiting http://localhost:8000/docs shows the API docs
	•	The frontend can successfully load data and display system status

📧 Support

For any issues or suggestions, please open an Issue or refer to the detailed documentation under the docs/ directory.

⸻


<div align="center">
  <sub>Built with ❤️ for sustainable business</sub>
</div>

