"""
Test script to verify Groq integration without GitHub
Run this to make sure your Groq API key works
"""

import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

# Initialize Groq client
groq_api_key = os.getenv("GROQ_API_KEY")

if not groq_api_key:
    print("❌ GROQ_API_KEY not found in .env file")
    print("Please set it at https://console.groq.com")
    exit(1)

print("✅ GROQ_API_KEY found")

try:
    client = Groq(api_key=groq_api_key)
    print("✅ Connected to Groq")
except Exception as e:
    print(f"❌ Failed to connect to Groq: {e}")
    exit(1)

# Test with sample code
sample_code = """
def calculate_factorial(n):
    result = 1
    for i in range(1, n):  # BUG: Should be range(1, n+1)
        result = result * i
    return result

# Security issue: hardcoded password
API_PASSWORD = "admin123"

# Style issue: variable name not descriptive
x = calculate_factorial(5)
print(x)
"""

prompt = f"""You are an expert code reviewer. Review this Python code:

```python
{sample_code}
```


Provide feedback on:
1. Bugs or logic errors
2. Security issues
3. Code style
4. Best practices

Keep it brief."""

print("\n🔍 Testing Groq API with sample code...")
print("=" * 50)

try:
    message = client.chat.completions.create(
        model="llama-3.1-8b-instant",  # Fast and good for code review
        max_tokens=300,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    
    response = message.choices[0].message.content
    print("✅ Groq API working!\n")
    print("Groq's Review:")
    print("-" * 50)
    print(response)
    print("-" * 50)
    print("\n✨ Everything looks good! You can start the backend.")
    
except Exception as e:
    print(f"❌ Error calling Groq API: {e}")
    print("\nMake sure:")
    print("1. GROQ_API_KEY is correct")
    print("2. Your Groq account has API access")
    print("3. You're not rate limited")
    exit(1)