#!/usr/bin/env python3
"""
Script to restore system state and trigger complete SASB analysis
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# Add the source directory to the path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from esg_encoding.api import system_components, app
from esg_encoding.models import ProcessingConfig
from esg_encoding.content_extractor import ContentExtractor
from esg_encoding.content_embedder import ContentEmbedder
from esg_encoding.metric_processor import MetricProcessor
from esg_encoding.dual_channel_retrieval import DualChannelRetrieval
from esg_encoding.disclosure_inference import DisclosureInferenceEngine
from loguru import logger

async def restore_and_analyze():
    """
    Restore system state and trigger complete analysis
    """
    logger.info("Starting system restoration...")
    
    # 1. Initialize all components
    config = ProcessingConfig(
        llm_api_key=os.getenv("LLM_API_KEY"),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        llm_model="qwen-plus-2025-07-28",
        embedding_model_name="BAAI/bge-m3",
        chunk_size=512,
        chunk_overlap=50
    )
    
    # Initialize components
    extractor = ContentExtractor(config)
    embedder = ContentEmbedder(config)
    processor = MetricProcessor(config)
    retrieval = DualChannelRetrieval(config)
    inference = DisclosureInferenceEngine(config)
    
    # Update system components
    system_components.update({
        "content_extractor": extractor,
        "content_embedder": embedder,
        "metric_processor": processor,
        "dual_channel_retrieval": retrieval,
        "disclosure_inference": inference
    })
    
    logger.info("Components initialized successfully")
    
    # 2. Check if we have processed report
    if system_components.get("report_content"):
        logger.info("Report already loaded in memory")
    else:
        # For now, we know the Dell report is processed and document_id is doc_dell (1)_cc3a559d
        # But we need to load it properly - let's see if we can find the processed data
        logger.warning("No report content in memory - this needs to be re-uploaded")
        return False
    
    # 3. Load SASB Semiconductors metrics
    logger.info("Loading SASB Semiconductors metrics...")
    metrics = processor.load_sasb_metrics_by_industry("Semiconductors")
    system_components["current_metrics"] = metrics
    logger.info(f"Loaded {len(metrics.metrics)} SASB Semiconductors metrics")
    
    # 4. Perform dual channel retrieval
    logger.info("Starting dual channel retrieval...")
    report_content = system_components["report_content"]
    retrieval_results = await retrieval.retrieve_for_all_metrics(metrics, report_content)
    logger.info(f"Retrieval completed for {len(retrieval_results)} metrics")
    
    # 5. Perform disclosure inference
    logger.info("Starting disclosure inference...")
    assessment = inference.analyze_compliance(
        retrieval_results=retrieval_results,
        report_content=report_content,
        report_file_path="dell (1).pdf",
        all_metrics=metrics
    )
    
    # Store the assessment
    system_components["current_assessment"] = assessment
    logger.info(f"Analysis completed! Overall score: {assessment.overall_compliance_score:.2%}")
    
    # 6. Generate and save report
    markdown_report = inference.generate_compliance_report(assessment)
    
    # Save report
    output_dir = Path(__file__).parent.parent / "outputs"
    output_dir.mkdir(exist_ok=True)
    
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"sasb_semiconductors_analysis_{timestamp}.md"
    report_path = output_dir / report_filename
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(markdown_report)
    
    logger.info(f"Report saved to: {report_path}")
    logger.info("System restoration and analysis completed successfully!")
    
    return True

if __name__ == "__main__":
    asyncio.run(restore_and_analyze())