import openai

def test_api_key():
    """Test the OpenAI API key with DashScope endpoint."""
    try:
        client = openai.OpenAI(
            api_key="sk-",
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )
        
        # Make a simple test request
        response = client.chat.completions.create(
            model="qwen-turbo",
            messages=[
                {"role": "user", "content": "Hello, this is a test message."}
            ],
            max_tokens=50
        )
        
        print("✅ API Key is working!")
        print(f"Response: {response.choices[0].message.content}")
        return True
        
    except openai.AuthenticationError:
        print("❌ Authentication failed - Invalid API key")
        return False
    except openai.APIError as e:
        print(f"❌ API Error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    test_api_key()
