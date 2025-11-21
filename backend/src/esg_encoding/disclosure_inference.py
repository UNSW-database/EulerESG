"""
Disclosure Inference Engine - Use LLM to analyze ESG metric disclosure status
"""

from asyncio.subprocess import Process
import json
from typing import List, Dict, Optional
from datetime import datetime
import openai
from loguru import logger

from .models import (
    ProcessingConfig, 
    MetricRetrievalResult,
    DisclosureStatus,
    DisclosureAnalysis,
    ComplianceAssessment,
    ReportContent,
    MetricCollection
)


class DisclosureInferenceEngine:
    """Disclosure Inference Engine - Call LLM to analyze disclosure status"""
    
    def __init__(self, config: ProcessingConfig):
        """
        Initialize disclosure inference engine
        
        Args:
            config: Processing configuration
        """
        self.config = config
        self.llm_client = self._init_llm_client()
        
    def _init_llm_client(self):
        """Initialize LLM client"""
        if not self.config.llm_api_key:
            raise ValueError("LLM API key is required for disclosure inference. Please configure LLM_API_KEY in your .env file.")
        
        print(f"DEBUG: config: {self.config}")

        client = openai.OpenAI(
            api_key=self.config.llm_api_key,
            base_url=self.config.llm_base_url if self.config.llm_base_url else "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )
        logger.info("LLM client initialized successfully for disclosure inference")
        return client
    
    def analyze_compliance(
        self,
        retrieval_results: List[MetricRetrievalResult],
        report_content: ReportContent,
        report_file_path: str = "",
        all_metrics: Optional[MetricCollection] = None,
        framework: Optional[str] = None,
        industry: Optional[str] = None,
        semi_industry: Optional[str] = None
    ) -> ComplianceAssessment:
        """
        Analyze compliance status of all metrics

        Args:
            retrieval_results: Dual-channel retrieval results
            report_content: Report content
            report_file_path: Report file path
            all_metrics: All metrics to analyze (if provided, will analyze all metrics)
            framework: Framework used (e.g., SASB, GRI)
            industry: Industry sector
            semi_industry: Sub-industry sector

        Returns:
            ComplianceAssessment: Compliance assessment report
        """
        
        # If all metrics are provided, analyze all metrics; otherwise only analyze retrieved metrics
        if all_metrics:
            logger.info(f"Starting compliance analysis for all {len(all_metrics.metrics)} metrics in collection")
            
            # Create retrieval results mapping
            retrieval_map = {result.metric_id: result for result in retrieval_results}
            
            metric_analyses = []
            for i, metric in enumerate(all_metrics.metrics):
                logger.info(f"Analyzing metric {i+1}/{len(all_metrics.metrics)}: {metric.metric_name}")
                
                # If retrieval results exist and matching content found, use retrieval analysis; otherwise mark as not disclosed
                #print("======== DEBUG METRIC STRUCTURE ========")
                #print(metric)
                if metric.metric_id in retrieval_map:
                    retrieval_result = retrieval_map[metric.metric_id]
                    # Only perform LLM analysis when matching content is actually found
                    if retrieval_result.total_matches > 0:
                        analysis = self._analyze_single_metric(retrieval_result, report_content, metric)
                    else:
                        # Retrieval result exists but no matching content, directly mark as not disclosed
                        analysis = DisclosureAnalysis(
                            metric_id=metric.metric_id,
                            metric_name=metric.metric_name,
                            metric_code=metric.metric_code,
                            disclosure_status=DisclosureStatus.NOT_DISCLOSED,
                            reasoning="No relevant metric content found",
                            evidence_segments=[],
                            improvement_suggestions=[],
                            # SASB display fields
                            category=getattr(metric, 'sasb_category', ''),
                            unit=getattr(metric, 'unit', ''),
                            type=getattr(metric, 'sasb_type', ''),
                            value=None,  # No value found
                            page=None   # No page found
                        )
                else:
                    # No relevant content retrieved, directly mark as not disclosed
                    analysis = DisclosureAnalysis(
                        metric_id=metric.metric_id,
                        metric_name=metric.metric_name,
                        metric_code=metric.metric_code,
                        disclosure_status=DisclosureStatus.NOT_DISCLOSED,
                        reasoning="No relevant metric content found",
                        evidence_segments=[],
                        improvement_suggestions=[],
                        # SASB display fields
                        category=getattr(metric, 'sasb_category', ''),
                        unit=getattr(metric, 'unit', ''),
                        type=getattr(metric, 'sasb_type', ''),
                        value=None,  # No value found
                        page=None   # No page found
                    )
                metric_analyses.append(analysis)
                
        else:
            logger.info(f"Starting compliance analysis for {len(retrieval_results)} retrieved metrics")
            
            # Analyze each retrieved metric
            metric_analyses = []
            for i, retrieval_result in enumerate(retrieval_results):
                logger.info(f"Analyzing metric {i+1}/{len(retrieval_results)}: {retrieval_result.metric_name}")
                analysis = self._analyze_single_metric(retrieval_result, report_content)
                metric_analyses.append(analysis)
        
        # Count quantities for each status
        disclosure_summary = {
            DisclosureStatus.FULLY_DISCLOSED: 0,
            DisclosureStatus.PARTIALLY_DISCLOSED: 0,
            DisclosureStatus.NOT_DISCLOSED: 0
        }
        
        for analysis in metric_analyses:
            disclosure_summary[analysis.disclosure_status] += 1
        
        # Calculate overall compliance score
        total_metrics = len(metric_analyses)
        if total_metrics > 0:
            fully_disclosed = disclosure_summary[DisclosureStatus.FULLY_DISCLOSED]
            partially_disclosed = disclosure_summary[DisclosureStatus.PARTIALLY_DISCLOSED]
            overall_score = (fully_disclosed * 1.0 + partially_disclosed * 0.5) / total_metrics
        else:
            overall_score = 0.0
        
        # Create assessment report
        assessment = ComplianceAssessment(
            report_id=report_content.document_id,
            total_metrics_analyzed=total_metrics,
            disclosure_summary=disclosure_summary,
            metric_analyses=metric_analyses,
            overall_compliance_score=overall_score,
            report_file_path=report_file_path,
            framework=framework,
            industry=industry,
            semi_industry=semi_industry
        )
        
        logger.info(f"Compliance analysis completed. Overall score: {overall_score:.2%}")
        logger.info(f"Disclosure summary - Fully: {disclosure_summary[DisclosureStatus.FULLY_DISCLOSED]}, "
                   f"Partially: {disclosure_summary[DisclosureStatus.PARTIALLY_DISCLOSED]}, "
                   f"Not disclosed: {disclosure_summary[DisclosureStatus.NOT_DISCLOSED]}")
        
        return assessment
    
    def _analyze_single_metric(
        self, 
        retrieval_result: MetricRetrievalResult,
        report_content: ReportContent,
        metric: Optional['ESGMetric'] = None
    ) -> DisclosureAnalysis:
        """
        Analyze disclosure status of a single metric
        
        Args:
            retrieval_result: Retrieval result for single metric
            report_content: Report content
            
        Returns:
            DisclosureAnalysis: Analysis result for this metric
        """
        # Get relevant segment content and tag information
        relevant_segments = []
        evidence_segment_ids = []
        segment_metadata = []

        # Get top 5 most relevant segments from combined results and add tag information
        for result in retrieval_result.combined_results[:5]:
            segment = self._get_segment_by_id(report_content, result.segment_id)
            if segment:
                # Add segment content
                relevant_segments.append(segment.content)
                evidence_segment_ids.append(result.segment_id)
                
                # Add segment tag information
                metadata = {
                    'segment_id': result.segment_id,
                    'page_number': segment.page_number,
                    'score': result.score,
                    'retrieval_type': result.retrieval_type,
                    'matched_keywords': result.matched_keywords
                }
                segment_metadata.append(metadata)
        
        # Build prompt containing tag information
        prompt = self._build_analysis_prompt(
            retrieval_result.metric_name,
            retrieval_result.metric_id,
            relevant_segments,
            segment_metadata
        )
        
        try:
            json_example = """
            {
              "metric_id": "CG-EC-130a.1",
              "disclosure_status": "fully_disclosed",
              "specific_data_found": ["511GJ consumed in Q1 (segment 1)"],
              "reasoning": "The report explicitly states the total energy consumption in GJ."
            }
            """

            system_prompt_text = f"""
            You are a professional ESG compliance analysis expert. Please analyze metric
            disclosure status based on the provided information.

            Respond ONLY with a JSON object in the following format. Do not include
            any other text, explanations, or markdown backticks.

            Example Format:
            {json_example}
            """
            
            system_prompt_json = f"""
            You are a professional ESG compliance analysis expert. Please analyze metric
            disclosure status based on the provided information.
            """

            FORCE_JSON = True # If model outputs thought train in response
            
            api_kwargs = {
                "model": self.config.llm_model,
                "temperature": 0.3  # CHANGE TO 1 FOR GPT-5
            }
            
            queries = []
            
            if (FORCE_JSON):
                api_kwargs["response_format"] = {"type": "json_object"}
                queries.append({"role": "system", "content": system_prompt_json})
                queries.append({"role": "user", "content": prompt})

                # (Optional) Add the assistant prefill for models like Claude
                # messages.append({"role": "assistant", "content": "{"}) 
            else:
                queries.append({"role": "system", "content": system_prompt_text})
                queries.append({"role": "user", "content": prompt})

            api_kwargs["messages"] = queries

            # Call LLM for analysis
            response = self.llm_client.chat.completions.create(
                **api_kwargs
            )
            
            #print("======== DEBUG LLM RESPONSE ========")
            #print(response.choices[0].message.content)
           
            if (FORCE_JSON):
                llm_output = ""
                if (response.choices[0].message.content[0] != "{"):
                    llm_output += "{"
                    # Some model (i.e. GPT-5) auto-completes the JSON
                
                llm_output += response.choices[0].message.content
                llm_result = json.loads(llm_output)

            else:
                llm_result = json.loads(response.choices[0].message.content)

            # Validate required fields from LLM
            if "reasoning" not in llm_result or not llm_result["reasoning"]:
                raise ValueError(f"LLM response missing required 'reasoning' field for metric {retrieval_result.metric_name}")

            # Classify disclosure status
            disclosure_status = self._classify_disclosure_status(llm_result)
            
            # Extract value and page from segment metadata
            found_value = None
            found_page = None
            if segment_metadata:
                # Use the highest scoring segment's page
                best_segment = max(segment_metadata, key=lambda x: x['score'])
                found_page = best_segment.get('page_number')
                # TODO: Extract actual value from segment content in future
                # For now, use the specific data found by LLM
                found_value_raw = llm_result.get("specific_data_found", None)
                # Convert to string if it's a list
                if isinstance(found_value_raw, list):
                    found_value = '; '.join(str(item) for item in found_value_raw) if found_value_raw else None
                else:
                    found_value = str(found_value_raw) if found_value_raw is not None else None
            
            # Create analysis result
            analysis = DisclosureAnalysis(
                metric_id=retrieval_result.metric_id,
                metric_name=retrieval_result.metric_name,
                metric_code=retrieval_result.metric_code,
                disclosure_status=disclosure_status,
                reasoning=llm_result["reasoning"],
                evidence_segments=evidence_segment_ids,
                improvement_suggestions=llm_result.get("improvement_suggestions", []),  # This field is optional
                # SASB display fields
                category=getattr(metric, 'sasb_category', '') if metric else '',
                unit=getattr(metric, 'unit', '') or '' if metric else '',
                type=getattr(metric, 'sasb_type', '') if metric else '',
                value=found_value,  # From LLM analysis
                page=found_page    # From search results
            )
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response for metric {retrieval_result.metric_name}: {e}")
            raise ValueError(f"LLM returned invalid JSON format: {e}")
        except Exception as e:
            logger.error(f"LLM analysis failed for metric {retrieval_result.metric_name}: {e}")
            logger.debug(f"Disclosure inference config: {self.config}")
            raise RuntimeError(f"LLM analysis error: {e}")
        finally:
            # print all config parameters for debugging
            logger.debug(f"Disclosure inference config: {self.config}")

        return analysis
    
    def _build_analysis_prompt(
        self, 
        metric_name: str, 
        metric_id: str,
        segments: List[str],
        segment_metadata: List[Dict] = None
    ) -> str:
        """
        Build LLM analysis prompt containing segment tag information
        
        Args:
            metric_name: Metric name
            metric_id: Metric ID
            segments: Related segment content
            segment_metadata: Segment metadata information
            
        Returns:
            str: Prompt text
        """
        # Build segment text containing tag information
        segments_text_parts = []
        for i, seg in enumerate(segments):
            segment_info = f"Segment {i+1}:\n{seg}"
            
            # If metadata exists, add tag information
            if segment_metadata and i < len(segment_metadata):
                meta = segment_metadata[i]
                segment_info += f"\n[Tag Info: Page {meta['page_number']}, Retrieval Type: {meta['retrieval_type']}, Match Score: {meta['score']:.3f}"
                if meta.get('matched_keywords'):
                    segment_info += f", Matched Keywords: {', '.join(meta['matched_keywords'])}"
                segment_info += "]"
            
            segments_text_parts.append(segment_info)
        
        segments_text = "\n\n".join(segments_text_parts)
        
        prompt = f"""As a professional ESG compliance analysis expert, please conduct a unified disclosure status analysis for the following metric.

Metric Information:
- Metric Name: {metric_name}
- Metric Code: {metric_id}

All Related Retrieved Segments (with tag information):
{segments_text if segments_text else "No related segments found"}

Please conduct a unified analysis based on all the above segment information, do not analyze each segment separately.

Analysis Requirements:
1. Comprehensively consider content and tag information of all segments
2. Pay attention to segment retrieval type (keyword/semantic) and matching scores
3. Provide overall disclosure status judgment for this metric

Return JSON format:
{{
    "has_disclosure": true/false,
    "disclosure_quality": "high/medium/low/none",
    "reasoning": "Comprehensive analysis reasoning based on all segments",
    "specific_data_found": "Specific data found from all segments",
    "improvement_suggestions": ["Suggestion 1", "Suggestion 2", ...]
}}
"""
        return prompt
    
    def _classify_disclosure_status(self, llm_response: dict) -> DisclosureStatus:
        """
        Perform three-category classification based on LLM analysis results
        
        Args:
            llm_response: JSON result returned by LLM
            
        Returns:
            DisclosureStatus: Disclosure status
        """
        has_disclosure = llm_response.get("has_disclosure", False)
        quality = llm_response.get("disclosure_quality", "none")
        
        if not has_disclosure or quality == "none":
            return DisclosureStatus.NOT_DISCLOSED
        elif quality == "high":
            return DisclosureStatus.FULLY_DISCLOSED
        else:  # medium or low
            return DisclosureStatus.PARTIALLY_DISCLOSED
    
    def _get_segment_by_id(self, report_content: ReportContent, segment_id: str):
        """
        Get segment content by ID
        
        Args:
            report_content: Report content
            segment_id: Segment ID
            
        Returns:
            TextSegment or None
        """
        for segment in report_content.document_content.segments:
            if segment.segment_id == segment_id:
                return segment
        return None
    
    def generate_compliance_report(self, assessment: ComplianceAssessment) -> str:
        """
        Generate Markdown format compliance report
        
        Args:
            assessment: Compliance assessment result
            
        Returns:
            str: Markdown format report
        """
        # Deduplicate metric analyses based on metric_id
        seen_metric_ids = set()
        unique_metric_analyses = []
        for analysis in assessment.metric_analyses:
            if analysis.metric_id not in seen_metric_ids:
                unique_metric_analyses.append(analysis)
                seen_metric_ids.add(analysis.metric_id)
        
        # Update assessment with deduplicated analyses
        total_unique_metrics = len(unique_metric_analyses)
        
        # Recalculate disclosure summary based on unique metrics
        disclosure_summary = {"fully_disclosed": 0, "partially_disclosed": 0, "not_disclosed": 0}
        for analysis in unique_metric_analyses:
            if analysis.disclosure_status.value == "fully_disclosed":
                disclosure_summary["fully_disclosed"] += 1
            elif analysis.disclosure_status.value == "partially_disclosed":
                disclosure_summary["partially_disclosed"] += 1
            else:
                disclosure_summary["not_disclosed"] += 1
        
        # Recalculate overall score
        overall_score = (disclosure_summary["fully_disclosed"] + 0.5 * disclosure_summary["partially_disclosed"]) / max(total_unique_metrics, 1)
        
        report = f"""# ESG Compliance Assessment Report

## Report Overview
- **Report ID**: {assessment.report_id}
- **Assessment Date**: {assessment.assessment_date.strftime("%Y-%m-%d %H:%M:%S")}
- **Analyzed Metrics**: {total_unique_metrics}
- **Overall Compliance Score**: {overall_score:.2%}

## Disclosure Status Statistics

| Disclosure Status | Count | Percentage |
|---------|------|------|
| Fully Disclosed | {disclosure_summary["fully_disclosed"]} | {disclosure_summary["fully_disclosed"] / max(total_unique_metrics, 1):.1%} |
| Partially Disclosed | {disclosure_summary["partially_disclosed"]} | {disclosure_summary["partially_disclosed"] / max(total_unique_metrics, 1):.1%} |
| Not Disclosed | {disclosure_summary["not_disclosed"]} | {disclosure_summary["not_disclosed"] / max(total_unique_metrics, 1):.1%} |

## Detailed Analysis Results

"""
        
        # Group by disclosure status using deduplicated metrics
        for status in [DisclosureStatus.NOT_DISCLOSED, DisclosureStatus.PARTIALLY_DISCLOSED, DisclosureStatus.FULLY_DISCLOSED]:
            status_metrics = [a for a in unique_metric_analyses if a.disclosure_status == status]
            
            if status_metrics:
                status_name = {
                    DisclosureStatus.FULLY_DISCLOSED: "✅ Fully Disclosed",
                    DisclosureStatus.PARTIALLY_DISCLOSED: "⚠️ Partially Disclosed",
                    DisclosureStatus.NOT_DISCLOSED: "❌ Not Disclosed"
                }[status]
                
                report += f"### {status_name}\n\n"
                
                for analysis in status_metrics:
                    report += f"#### {analysis.metric_name} ({analysis.metric_code})\n"
                    report += f"- **Analysis Reasoning**: {analysis.reasoning}\n"
                    
                    if analysis.evidence_segments:
                        report += f"- **Evidence Segments**: {', '.join(analysis.evidence_segments[:3])}\n"
                    
                    if analysis.improvement_suggestions:
                        report += "- **Improvement Suggestions**:\n"
                        for suggestion in analysis.improvement_suggestions:
                            report += f"  - {suggestion}\n"
                    
                    report += "\n"
        
        report += """## Improvement Recommendations Summary

Based on the analysis results, it is recommended to prioritize improvements in the following areas:
1. For undisclosed metrics, recommend adding relevant content in the next report
2. For partially disclosed metrics, recommend providing more detailed quantitative data
3. Recommend adopting standardized ESG reporting frameworks to improve completeness and comparability of disclosures
"""
        
        return report
