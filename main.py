from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import os
import json
import hmac
import hashlib
from datetime import datetime
import httpx
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

# Initialize FastAPI app
app = FastAPI(title="Code Review Bot", version="1.0")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for now
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Groq client
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# GitHub App credentials
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_PRIVATE_KEY = os.getenv("GITHUB_PRIVATE_KEY")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")

# For now, we'll use a simple token (we'll upgrade to App later)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Simple in-memory storage for reviews (we'll add database later)
reviews_db = []


class Review(BaseModel):
    repo: str
    pr_number: int
    file_path: str
    feedback: str
    severity: str  # "critical", "warning", "info"
    created_at: str
class CodeSubmission(BaseModel):
    code: str
    language: Optional[str] = "python"
    filename: Optional[str] = "submitted_code"

def verify_github_webhook(request_body: bytes, signature: str) -> bool:
    """Verify that the webhook came from GitHub"""
    if not GITHUB_WEBHOOK_SECRET:
        print("WARNING: GITHUB_WEBHOOK_SECRET not set, skipping verification")
        return True
    
    expected_signature = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        request_body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected_signature)


async def get_pr_files(repo_owner: str, repo_name: str, pr_number: int) -> list:
    """Fetch the list of files changed in a PR"""
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/files"
    
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error fetching PR files: {response.status_code}")
            return []


async def get_file_content(repo_owner: str, repo_name: str, file_path: str, ref: str) -> str:
    """Fetch the content of a file from GitHub"""
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}?ref={ref}"
    
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.raw"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        if response.status_code == 200:
            return response.text
        else:
            return ""


def review_code_with_groq(file_path: str, code: str, language: str) -> dict:
    """Send code to Groq for AI review"""
    
    prompt = f"""You are an expert code reviewer. Review the following {language} code and provide feedback.

File: {file_path}

Code:
```{language}
{code}
```


Provide your review in this exact format:
SEVERITY: [critical/warning/info]
FEEDBACK: [Your detailed feedback - be specific about what could be improved]

Focus on:
1. Security issues
2. Performance problems
3. Code style and readability
4. Best practices
5. Potential bugs

Keep feedback concise but actionable."""

    try:
        message = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",  # Fast and good for code review
            max_tokens=500,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        response_text = message.content[0].message.content
        
        # Parse the response
        lines = response_text.split('\n')
        severity = "info"
        feedback = response_text
        
        for line in lines:
            if line.startswith("SEVERITY:"):
                severity = line.replace("SEVERITY:", "").strip().lower()
            elif line.startswith("FEEDBACK:"):
                feedback = line.replace("FEEDBACK:", "").strip()
        
        return {
            "feedback": feedback,
            "severity": severity,
            "raw_response": response_text
        }
    except Exception as e:
        print(f"Error calling Groq: {e}")
        return {
            "feedback": "Could not analyze code due to API error",
            "severity": "error",
            "raw_response": str(e)
        }


def get_file_language(file_path: str) -> str:
    """Determine programming language from file extension"""
    extensions = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".cpp": "cpp",
        ".c": "c",
        ".rb": "ruby",
        ".php": "php",
        ".sql": "sql",
        ".html": "html",
        ".css": "css",
    }
    
    ext = os.path.splitext(file_path)[1].lower()
    return extensions.get(ext, "plaintext")


@app.get("/")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "message": "Code Review Bot is running"}


@app.post("/webhook/github")
async def github_webhook(request: Request):
    """
    GitHub webhook endpoint
    This is called whenever certain events happen in a repo
    """
    
    # Get the raw body for signature verification
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    
    # Verify the webhook came from GitHub
    if not verify_github_webhook(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Parse the JSON payload
    try:
        payload = json.loads(body)
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    # We only care about "opened" and "synchronize" (updated) PR events
    action = payload.get("action")
    if action not in ["opened", "synchronize"]:
        return {"status": "ignored", "reason": f"Ignoring action: {action}"}
    
    # Extract PR information
    pr_data = payload.get("pull_request", {})
    if not pr_data:
        return {"status": "error", "reason": "No PR data in webhook"}
    
    repo = payload.get("repository", {})
    repo_owner = repo.get("owner", {}).get("login")
    repo_name = repo.get("name")
    pr_number = pr_data.get("number")
    pr_head_sha = pr_data.get("head", {}).get("sha")
    
    print(f"\n🔍 Reviewing PR #{pr_number} in {repo_owner}/{repo_name}")
    
    # Get the files changed in this PR
    files = await get_pr_files(repo_owner, repo_name, pr_number)
    
    if not files:
        print("No files found in PR")
        return {"status": "no_files"}
    
    # Review each file
    reviews = []
    for file_info in files[:5]:  # Limit to first 5 files for now
        file_path = file_info.get("filename")
        patch = file_info.get("patch", "")
        
        # Skip certain files
        if file_path.endswith((".md", ".txt", ".json", ".lock")):
            print(f"⏭️  Skipping {file_path}")
            continue
        
        print(f"📄 Analyzing {file_path}")
        
        # Get file content
        file_content = await get_file_content(repo_owner, repo_name, file_path, pr_head_sha)
        
        if not file_content:
            print(f"Could not fetch {file_path}")
            continue
        
        # Limit content size for API (avoid huge files)
        if len(file_content) > 5000:
            file_content = file_content[:5000] + "\n... (truncated)"
        
        # Determine language
        language = get_file_language(file_path)
        
        # Review with Groq
        review = review_code_with_groq(file_path, file_content, language)
        
        # Store review
        review_obj = Review(
            repo=f"{repo_owner}/{repo_name}",
            pr_number=pr_number,
            file_path=file_path,
            feedback=review["feedback"],
            severity=review["severity"],
            created_at=datetime.now().isoformat()
        )
        reviews.append(review_obj)
        reviews_db.append(review_obj.dict())
        
        print(f"✅ {file_path}: {review['severity'].upper()}")
    
    print(f"\n✨ Completed review of PR #{pr_number}")
    
    return {
        "status": "success",
        "pr_number": pr_number,
        "files_reviewed": len(reviews),
        "reviews": [r.dict() for r in reviews]
    }


@app.get("/reviews")
async def get_reviews(repo: Optional[str] = None):
    """Get all stored reviews, optionally filtered by repo"""
    if repo:
        return [r for r in reviews_db if r["repo"] == repo]
    return reviews_db


@app.get("/reviews/stats")
async def get_stats():
    """Get statistics about reviews"""
    total = len(reviews_db)
    critical = len([r for r in reviews_db if r["severity"] == "critical"])
    warnings = len([r for r in reviews_db if r["severity"] == "warning"])
    
    return {
        "total_reviews": total,
        "critical_issues": critical,
        "warnings": warnings,
        "repos_reviewed": len(set(r["repo"] for r in reviews_db))
    }
@app.post("/review")
async def manual_review(submission: CodeSubmission):
    """Manually submit code for review (used by frontend demo)"""
    review = review_code_with_groq(submission.filename, submission.code, submission.language)
    return {
        "feedback": review["feedback"],
        "severity": review["severity"],
        "language": submission.language
    }
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)