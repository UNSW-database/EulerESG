# Backend bugs

## During SASB analysis workflow,

- At `_analyze_single_metric`, line 271:
```
| ERROR    | esg_encoding.disclosure_inference:_analyze_single_metric:271 - LLM analysis failed for metric Description of approach to identifying and addressing data security risks in products: Error code: 404 - {'error': {'message': 'The model `qwen-plus-2025-07-28` does not exist or you do not have access to it.', 'type': 'invalid_request_error', 'code': 'model_not_found'}}
```

- At `upload_report`, line 298:
```
| ERROR    | esg_encoding.api:upload_report:298 - Error in assessment processing: LLM analysis error: Error code: 404 - {'error': {'message': 'The model `qwen-plus-2025-07-28` does not exist or you do not have access to it.', 'type': 'invalid_request_error', 'code': 'model_not_found'}}
```

Error caused by

```py
response = self.llm_client.chat.completions.create(
                model=self.config.llm_model,
                messages=[
                    {"role": "system", "content": "You are a professional ESG compliance analysis expert. Please analyze metric disclosure status based on the provided information."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3
            )
```






