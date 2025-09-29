#!/usr/bin/env python3
"""
ESG Backend System Setup Script
Run this script after cloning the repository to set up the environment
"""

import os
import sys
import shutil
from pathlib import Path

def create_directories():
    """Create required directories if they don't exist"""
    directories = [
        "uploads/reports/pending",
        "uploads/reports/processed",
        "uploads/metrics",
        "uploads/outputs",
        "outputs",
        "logs",
        "temp",
        "models"
    ]

    print("Creating required directories...")
    for dir_path in directories:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        print(f"  ✓ {dir_path}")

def setup_config():
    """Set up configuration files"""
    config_dir = Path("config")
    env_example = config_dir / ".env.example"
    env_file = config_dir / ".env"

    if env_example.exists() and not env_file.exists():
        shutil.copy(env_example, env_file)
        print("\n✓ Created .env file from .env.example")
        print("⚠️  Please edit config/.env and add your API keys")
    elif env_file.exists():
        print("\n✓ .env file already exists")
    else:
        print("\n⚠️  .env.example not found")

def check_python_version():
    """Check if Python version meets requirements"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 10):
        print(f"⚠️  Python 3.10+ required, current version: {sys.version}")
        return False
    print(f"✓ Python version: {sys.version}")
    return True

def install_dependencies():
    """Install Python dependencies"""
    print("\nInstalling dependencies...")
    os.system(f"{sys.executable} -m pip install --upgrade pip")
    os.system(f"{sys.executable} -m pip install -r requirements.txt")
    print("✓ Dependencies installed")

def download_models():
    """Pre-download sentence transformer models"""
    print("\nDownloading language models (this may take a few minutes)...")
    try:
        from sentence_transformers import SentenceTransformer
        model_name = "sentence-transformers/all-MiniLM-L6-v2"
        print(f"  Downloading {model_name}...")
        SentenceTransformer(model_name, cache_folder="./models")
        print("  ✓ Model downloaded successfully")
    except ImportError:
        print("  ⚠️  Please install dependencies first")
    except Exception as e:
        print(f"  ⚠️  Model download failed: {e}")
        print("  The model will be downloaded on first run")

def main():
    """Main setup function"""
    print("=" * 50)
    print("ESG Backend System Setup")
    print("=" * 50)

    # Check Python version
    if not check_python_version():
        print("\n⚠️  Please install Python 3.10 or higher")
        sys.exit(1)

    # Create directories
    create_directories()

    # Setup config
    setup_config()

    # Ask if user wants to install dependencies
    response = input("\nDo you want to install Python dependencies? (y/n): ")
    if response.lower() == 'y':
        install_dependencies()

        # Ask if user wants to download models
        response = input("\nDo you want to pre-download language models? (y/n): ")
        if response.lower() == 'y':
            download_models()

    print("\n" + "=" * 50)
    print("Setup complete!")
    print("=" * 50)
    print("\nNext steps:")
    print("1. Edit config/.env and add your LLM API key")
    print("2. Run the backend: python scripts/start_backend.py")
    print("3. Access the API at: http://localhost:8000/docs")

if __name__ == "__main__":
    main()