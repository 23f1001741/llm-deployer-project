import os
import threading
import time
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from openai import OpenAI
from github import Github, GithubException

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# --- GitHub Actions Workflow ---
GITHUB_WORKFLOW_YAML = """
name: Deploy to GitHub Pages
on:
  push:
    branches: [ main ]
jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout 🛎️
        uses: actions/checkout@v3
      - name: Setup Pages
        uses: actions/configure-pages@v3
      - name: Upload artifact
        uses: actions/upload-pages-artifact@v2
        with:
          path: '.'
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v2
"""

# --- MIT License Text ---
MIT_LICENSE = """
MIT License
Copyright (c) 2025 [Your Name or Username]
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

def notify_evaluator(url, payload):
    """
    Sends a POST request to the evaluation URL with exponential backoff retry logic.
    """
    delay = 1
    max_retries = 5
    for i in range(max_retries):
        try:
            print(f"📡 Notifying evaluator at {url} (Attempt {i+1})...")
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                print("✅ Successfully notified evaluator.")
                return True
            print(f"⚠️ Notification failed with status {response.status_code}. Retrying in {delay}s...")
        except requests.exceptions.RequestException as e:
            print(f"❌ An error occurred during notification: {e}. Retrying in {delay}s...")
        
        time.sleep(delay)
        delay *= 2 # Exponential backoff: 1, 2, 4, 8 seconds
    
    print("❌ Could not notify evaluator after multiple retries.")
    return False

def process_build_task(data):
    """
    Handles the entire build and deploy process.
    """
    print("--------------------------------------------------")
    task_id = data.get('task')
    print(f"🚀 Starting to process task: {task_id}")

    try:
        # --- 1. LLM CODE GENERATION ---
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url="https://aipipe.org/openai/v1")
        brief = data.get('brief')
        checks = str(data.get('checks', ''))

        code_prompt = f"""
        You are an expert web developer. Create a single, complete index.html file.
        Brief: {brief}
        The final code must be functional and self-contained.
        The final code must pass these checks: {checks}
        Generate only the full HTML file content and nothing else.
        """
        
        code_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": code_prompt}]
        )
        generated_html = code_response.choices[0].message.content.strip()
        print("✅ LLM generated the HTML code.")

        # --- NEW: LLM README.md GENERATION ---
        readme_prompt = f"""
        You are a technical writer. Create a professional README.md for a project.
        Project Brief: {brief}
        The project was implemented with the following code:
        ```html
        {generated_html}
        ```
        Generate a complete README.md file with the following sections:
        - A project title.
        - A brief one-paragraph summary of what the application does.
        - A "How It Works" section explaining the code's functionality.
        - A "License" section mentioning it is MIT licensed.
        Generate only the markdown content for the file.
        """
        
        readme_response = client.chat.completions.create(
            model="gpt-4o", # Using gpt-4o is great for quality
            messages=[{"role": "user", "content": readme_prompt}]
        )
        generated_readme = readme_response.choices[0].message.content.strip()
        print("✅ LLM generated a professional README.md.")

        # --- 2. GITHUB REPO CREATION ---
        g = Github(os.getenv("GITHUB_PAT"))
        user = g.get_user()
        repo_name = f"llm-app-{task_id}"
        
        try:
            old_repo = user.get_repo(repo_name)
            old_repo.delete()
            print(f"🗑️ Deleted existing repo: {repo_name}")
        except GithubException:
            pass 

        repo = user.create_repo(repo_name, private=False)
        print(f"✅ Created new GitHub repo: {repo.full_name}")

        repo.create_file("LICENSE", "feat: add MIT license", MIT_LICENSE)
        repo.create_file("README.md", "docs: generate professional readme", generated_readme) # Use the new README
        repo.create_file("index.html", "feat: add application code", generated_html)
        repo.create_file(".github/workflows/deploy.yml", "ci: add GitHub Pages deployment workflow", GITHUB_WORKFLOW_YAML)
        
        print("✅ Pushed all necessary files to the repo.")

        main_branch = repo.get_branch("main")
        commit_sha = main_branch.commit.sha
        print(f"🔑 Commit SHA: {commit_sha}")
        
        repo_url = repo.html_url
        pages_url = f"https://{user.login}.github.io/{repo_name}/"
        print(f"🌐 Pages URL: {pages_url}")

        # --- 3. NOTIFY EVALUATION SERVER ---
        evaluation_url = data.get('evaluation_url')
        if evaluation_url:
            payload = {
                "email": data.get('email'),
                "task": task_id,
                "round": data.get('round'),
                "nonce": data.get('nonce'),
                "repo_url": repo_url,
                "commit_sha": commit_sha,
                "pages_url": pages_url,
            }
            notify_evaluator(evaluation_url, payload)
        else:
            print("⚠️ No evaluation_url provided in the request. Skipping notification.")

    except Exception as e:
        print(f"❌ An error occurred: {e}")
    
    print("--------------------------------------------------")


@app.route('/api-endpoint', methods=['POST'])
def handle_build_request():
    data = request.get_json()
    expected_secret = os.getenv("MY_APP_SECRET")
    if not data or data.get('secret') != expected_secret:
        return jsonify({"error": "Unauthorized"}), 403

    thread = threading.Thread(target=process_build_task, args=(data,))
    thread.start()
    
    return jsonify({"status": "Request received and is being processed."}), 200


if __name__ == '__main__':
    app.run(port=5000, debug=True)