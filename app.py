from flask import Flask, render_template, request, jsonify
import requests
import json
import os
from datetime import datetime
import jsonschema
from jsonschema import validate

app = Flask(__name__)

API_KEY = "sk-KNo006G2a48UVE3IxFlQEQ"
API_URL = "https://api.gpt.mws.ru/v1/chat/completions"
AVAILABLE_MODELS = [
    'mws-gpt-alpha',
    'qwen2.5-32b-instruct',
    'llama-3.3-70b-instruct',
    'llama-3.1-8b-instruct',
    'qwen2.5-72b-instruct',
    'gemma-3-27b-it',
    'deepseek-r1-distill-qwen-32b'
]
SELECTED_MODEL = 'qwen2.5-32b-instruct'

current_schema = None
integration_steps = []

def log_api_debug(info_type, data):
    """Логирование отладочной информации"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{timestamp}] {info_type}:")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("-" * 50)

def validate_json_schema(schema):
    """Проверяет валидность JSON-схемы"""
    try:
        if isinstance(schema, str):
            schema = json.loads(schema)
        jsonschema.Draft7Validator.check_schema(schema)
        return True, None
    except jsonschema.exceptions.SchemaError as e:
        return False, str(e)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {str(e)}"

def check_missing_fields(schema, prompt):
    """Проверяет наличие обязательных полей в схеме"""
    required_fields = ["title", "type", "properties"]
    missing = [field for field in required_fields if field not in schema]
    return missing

def extract_integration_steps(schema):
    """Извлекает шаги интеграции из схемы"""
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except json.JSONDecodeError:
            return []
    
    return schema.get("integrationSteps", [])

def generate_json_schema(prompt, is_refinement=False):
    """Генерирует JSON-схему на основе промпта"""
    global current_schema, integration_steps
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    system_msg = """Ты генератор JSON-схем. Возвращай ТОЛЬКО валидный JSON Schema без каких-либо пояснений. 
    Схема должна включать title, type и properties. Если в запросе упоминаются шаги интеграции, 
    добавь их в массив integrationSteps."""
    
    if is_refinement and current_schema:
        system_msg += f"\nТекущая схема: {json.dumps(current_schema)}. Внеси изменения согласно запросу."

    data = {
        "model": SELECTED_MODEL,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"}
    }
    
    try:
        response = requests.post(API_URL, headers=headers, json=data, timeout=15)
        response_data = response.json()
        
        if response.status_code != 200:
            error_msg = response_data.get('error', {}).get('message', 'Unknown error')
            return {
                "error": "API Error",
                "message": error_msg,
                "status_code": response.status_code
            }
        
        content = response_data['choices'][0]['message']['content']
        
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                is_valid, error = validate_json_schema(parsed)
                if not is_valid:
                    return {
                        "error": "Invalid Schema",
                        "message": error,
                        "raw_response": content
                    }
                
                current_schema = parsed
                integration_steps = extract_integration_steps(parsed)
                return parsed
            except json.JSONDecodeError as e:
                return {
                    "error": "Invalid JSON from API",
                    "raw_response": content
                }
        return content
        
    except Exception as e:
        return {
            "error": "Request failed",
            "details": str(e),
            "type": type(e).__name__
        }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/models')
def list_models():
    return jsonify({
        "available_models": AVAILABLE_MODELS,
        "selected_model": SELECTED_MODEL
    })

@app.route('/generate-schema', methods=['POST'])
def handle_generate_schema():
    global current_schema, integration_steps
    
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400
        
    data = request.json
    prompt = data.get('prompt')
    action = data.get('action', 'generate')
    
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400
    
    if action == 'generate':
        result = generate_json_schema(prompt, is_refinement=False)
    else:
        result = generate_json_schema(prompt, is_refinement=True)
        
        if 'шаг интеграции' in prompt.lower() or 'integration step' in prompt.lower():
            import re
            steps_mentioned = re.findall(r'шаг интеграции\s+"([^"]+)"', prompt.lower()) + \
                            re.findall(r"шаг интеграции\s+'([^']+)'", prompt.lower()) + \
                            re.findall(r'integration step\s+"([^"]+)"', prompt.lower()) + \
                            re.findall(r"integration step\s+'([^']+)'", prompt.lower())
            
            if steps_mentioned:
                missing_steps = [step for step in steps_mentioned 
                               if step not in integration_steps]
                if missing_steps:
                    return jsonify({
                        "schema": json.dumps(result, indent=2, ensure_ascii=False),
                        "warning": f"Шаги интеграции не найдены: {', '.join(missing_steps)}",
                        "available_steps": integration_steps
                    })
    
    if isinstance(result, dict) and 'error' in result:
        return jsonify({
            "error": result.get("message", "API Error"),
            "response": result.get("raw_response", "")
        }), 400
    
    missing_fields = check_missing_fields(result, prompt)
    if missing_fields:
        return jsonify({
            "schema": json.dumps(result, indent=2, ensure_ascii=False),
            "missing_fields": missing_fields,
            "message": "Требуется уточнение для недостающих полей"
        })
    
    return jsonify({
        "schema": json.dumps(result, indent=2, ensure_ascii=False),
        "success": True,
        "integration_steps": integration_steps
    })

@app.route('/check-integration', methods=['POST'])
def check_integration():
    step = request.json.get('step')
    if not step:
        return jsonify({"error": "Integration step is required"}), 400
    
    if step in integration_steps:
        return jsonify({"exists": True})
    else:
        return jsonify({
            "exists": False,
            "available_steps": integration_steps
        })

if __name__ == '__main__':
    print(f"\n{'='*50}")
    print(f"Starting API Schema Generator")
    print(f"Using model: {SELECTED_MODEL}")
    print(f"Available models: {AVAILABLE_MODELS}")
    print(f"{'='*50}\n")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
