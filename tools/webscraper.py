import json
import urllib.request
import re

def lambda_handler(event, context):
    """
    Standard library web scraper to fetch website text for the agent.
    Optimized to return clean text without wasting tokens on HTML tags.
    """
    try:
        # Extract URL from the event payload from Bedrock
        # Depending on how Bedrock formats the tool call, it might be nested
        url = event.get('url')
        if not url and 'parameters' in event:
            # Check for parameters if formatted as an Agent Action Group
            url = next((param['value'] for param in event['parameters'] if param['name'] == 'url'), None)
            
        if not url:
            return {"statusCode": 400, "body": "No URL provided to scrape."}

        # Use a standard user-agent so websites don't block the request
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8')
        
        # Strip all HTML tags using regex to get plain text
        clean_text = re.sub('<[^<]+?>', ' ', html_content)
        # Collapse multiple spaces and newlines into single spaces
        clean_text = ' '.join(clean_text.split())
        
        # Return only the first 2500 characters to heavily optimize the Token Bonus!
        return {
            "statusCode": 200,
            "body": clean_text[:2500] 
        }

    except Exception as e:
        return {"statusCode": 500, "body": f"Error scraping website: {str(e)}"}