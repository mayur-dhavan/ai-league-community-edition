import sys
import io

def lambda_handler(event, context):
    """
    Executes Python code provided by the agent and returns the printed output.
    Used for complex mathematical challenges like Blue Brain (c2).
    """
    try:
        # Extract the code from the AgentCore payload
        code = event.get('code')
        if not code and 'parameters' in event:
            code = next((param['value'] for param in event['parameters'] if param['name'] == 'code'), None)
            
        if not code:
            return {"statusCode": 400, "body": "No code provided to execute."}

        # Redirect standard output to capture print statements from the executed code
        old_stdout = sys.stdout
        redirected_output = sys.stdout = io.StringIO()
        
        try:
            # Execute the code in isolated namespaces
            exec(code, {}, {})
            output = redirected_output.getvalue()
        except Exception as e:
            output = f"Execution Error: {str(e)}"
        finally:
            # Always restore standard output
            sys.stdout = old_stdout
            
        # Limit output length to save on the Token Bonus
        final_output = output.strip()[:1000] if output else "Executed successfully but printed nothing."
        
        return {
            "statusCode": 200,
            "body": final_output
        }

    except Exception as e:
        return {"statusCode": 500, "body": f"System Error: {str(e)}"}