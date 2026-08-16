import requests
import json

# Step 1: Apply for access
print("Step 1: Applying for access...")
apply_data = {
    "name": "Shaik Shanum Shazfa",
    "email": "shanumshaik2@gmail.com",
    "phone": "+91 8919435288",
    "whatsapp": "+91 8919435288",
    "linkedin_url": "https://www.linkedin.com/in/shanum-shaik-7857673b5/"
}

response = requests.post(
    "https://pseudogram-api.onrender.com/v1/apply",
    json=apply_data,
    headers={"Content-Type": "application/json"}
)

print(f"Status: {response.status_code}")
print(f"Response: {response.text}")

if response.status_code == 200:
    print("\n✓ Application successful!")
    
    # Step 2: Get API key
    print("\nStep 2: Getting API key...")
    key_response = requests.post(
        "https://pseudogram-api.onrender.com/v1/keygen",
        json={"email": "shanumshaik2@gmail.com"},
        headers={"Content-Type": "application/json"}
    )
    
    print(f"Status: {key_response.status_code}")
    print(f"Response: {key_response.text}")
    
    if key_response.status_code == 200:
        data = key_response.json()
        api_key = data.get("api_key")
        print(f"\n✓ Your API Key: {api_key}")
        print("\nAdd this to your .env file:")
        print(f"API_KEY={api_key}")
    else:
        print("\n✗ Failed to get API key. Wait a moment and try again.")
else:
    print("\n✗ Application failed. Check the response above.")
