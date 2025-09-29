#!/usr/bin/env python3
"""
Script to save the current assessment data to JSON for frontend
"""

import json
import sys
from pathlib import Path

# Add the source directory to the path  
sys.path.append(str(Path(__file__).parent.parent / "src"))

from esg_encoding.api import system_components
from loguru import logger

def save_current_assessment():
    """Save current assessment to JSON file"""
    
    # Check if there's a current assessment
    current_assessment = system_components.get("current_assessment")
    if not current_assessment:
        logger.error("No current assessment found in memory")
        return False
    
    logger.info(f"Found assessment: {current_assessment.report_id}")
    logger.info(f"Total metrics: {current_assessment.total_metrics_analyzed}")
    logger.info(f"Overall score: {current_assessment.overall_compliance_score:.2%}")
    
    # Extract file ID from report ID
    # Format: doc_20250826_042708_ffd688f6-e1aa-49d3-be2d-2eefdc6ccfd2_9b591e1c
    report_id_parts = current_assessment.report_id.split('_')
    if len(report_id_parts) >= 4:
        file_id = '_'.join(report_id_parts[3:4])  # Extract the UUID part
    else:
        file_id = current_assessment.report_id
    
    logger.info(f"Extracted file ID: {file_id}")
    
    # Create uploads directory structure
    uploads_dir = Path("../../uploads")
    json_report_dir = uploads_dir / "outputs" / "compliance_reports" 
    json_report_dir.mkdir(parents=True, exist_ok=True)
    
    json_report_path = json_report_dir / f"{file_id}_compliance.json"
    
    # Convert assessment to JSON format
    assessment_json = {
        "report_id": current_assessment.report_id,
        "assessment_date": current_assessment.assessment_date.isoformat(),
        "total_metrics": current_assessment.total_metrics_analyzed,
        "overall_score": current_assessment.overall_compliance_score,
        "disclosure_summary": {
            "fully_disclosed": current_assessment.disclosure_summary.get("fully_disclosed", 0),
            "partially_disclosed": current_assessment.disclosure_summary.get("partially_disclosed", 0), 
            "not_disclosed": current_assessment.disclosure_summary.get("not_disclosed", 0)
        },
        "metric_analyses": []
    }
    
    # Add metric analyses
    for analysis in current_assessment.metric_analyses:
        metric_json = {
            "metric_id": analysis.metric_id,
            "metric_name": analysis.metric_name,
            "disclosure_status": analysis.disclosure_status.value if hasattr(analysis.disclosure_status, 'value') else str(analysis.disclosure_status),
            "reasoning": analysis.reasoning,
            "unit": getattr(analysis, 'unit', ''),
            "category": getattr(analysis, 'category', ''),
            "topic": getattr(analysis, 'topic', ''),
            "type": getattr(analysis, 'type', ''),
            "page": getattr(analysis, 'relevant_pages', None),
            "context": getattr(analysis, 'relevant_context', None)
        }
        assessment_json["metric_analyses"].append(metric_json)
    
    # Save JSON file
    with open(json_report_path, "w", encoding="utf-8") as f:
        json.dump(assessment_json, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Assessment JSON saved to: {json_report_path}")
    logger.info(f"File size: {json_report_path.stat().st_size} bytes")
    
    return True

if __name__ == "__main__":
    success = save_current_assessment()
    if success:
        print("Assessment JSON created successfully!")
    else:
        print("Failed to create assessment JSON")