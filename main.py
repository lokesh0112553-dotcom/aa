"""
Railway.app Hostable Shopify Auto Checker API
Endpoint: /sh?cc=CARD|MONTH|YEAR|CVV&url=STORE_URL&proxy=PROXY
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
import asyncio
import random
import json
import os
from urllib.parse import urlparse, parse_qs
from typing import Optional
import base64
import time

# अपने shopify_auto.py से फंक्शन इम्पोर्ट करें
from shopify_auto import shopify_auto_check, parse_response, get_product_id, extract_storefront_token

app = FastAPI(title="Shopify Auto Checker API", version="2.0")

# कैशिंग के लिए स्टोरेज (वैकल्पिक)
product_cache = {}
token_cache = {}

def parse_cc_string(cc_string: str) -> dict:
    """Parse CC string in format: CC|MONTH|YEAR|CVV"""
    try:
        parts = cc_string.split('|')
        if len(parts) >= 4:
            return {
                "number": parts[0].strip(),
                "month": parts[1].strip().zfill(2),
                "year": parts[2].strip(),
                "cvv": parts[3].strip()
            }
        return None
    except:
        return None

def parse_proxy_string(proxy_string: str) -> Optional[str]:
    """Parse proxy string in format: ip:port:user:pass or ip:port"""
    try:
        parts = proxy_string.split(':')
        if len(parts) == 4:
            # ip:port:user:pass
            return f"{parts[0]}:{parts[1]}:{parts[2]}:{parts[3]}"
        elif len(parts) == 2:
            # ip:port
            return proxy_string
        elif len(parts) == 1 and proxy_string:
            # सिर्फ ip (डिफॉल्ट पोर्ट 8080)
            return f"{proxy_string}:8080"
        return None
    except:
        return None

@app.get("/")
async def root():
    return {
        "status": "active",
        "endpoints": {
            "/sh": "GET - Check credit card",
            "/health": "GET - Health check"
        },
        "example": "/sh?cc=4410300262666845|10|27|230&url=https://www.lliked.com&proxy=175.29.133.8:5433:799JRELTBPAE:F7BQ7D3EQSQA",
        "response_format": {
            "Response": "APPROVED/DECLINED/Error message",
            "CC": "4410300262666845|10|27|230",
            "Price": "3.99",
            "Gate": "storefront_token_or_error",
            "Site": "https://www.lliked.com",
            "Raw_Response": "original_response_from_shopify",
            "Proxy_Alive": "Yes/No"
        }
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}

@app.get("/sh")
async def check_card(request: Request):
    """
    Main endpoint to check credit card on Shopify store
    Parameters:
    - cc: Card string in format NUMBER|MONTH|YEAR|CVV
    - url: Shopify store URL
    - proxy: (optional) Proxy in format ip:port:user:pass or ip:port
    """
    try:
        # Get query parameters
        params = request.query_params
        cc_string = params.get("cc")
        store_url = params.get("url")
        proxy_string = params.get("proxy")
        
        # Validate required parameters
        if not cc_string or not store_url:
            return JSONResponse(
                status_code=400,
                content={
                    "Response": "Error: Missing parameters",
                    "CC": cc_string or "",
                    "Price": "0",
                    "Gate": "missing_parameters",
                    "Site": store_url or "",
                    "Proxy_Alive": "No"
                }
            )
        
        # Parse CC
        card_info = parse_cc_string(cc_string)
        if not card_info:
            return JSONResponse(
                status_code=400,
                content={
                    "Response": "Error: Invalid CC format. Use: NUMBER|MONTH|YEAR|CVV",
                    "CC": cc_string,
                    "Price": "0",
                    "Gate": "invalid_cc_format",
                    "Site": store_url,
                    "Proxy_Alive": "No"
                }
            )
        
        # Parse proxy if provided
        proxy = None
        if proxy_string:
            proxy = parse_proxy_string(proxy_string)
        
        # Extract domain for cache key
        parsed_url = urlparse(store_url)
        domain = parsed_url.netloc or store_url.replace("https://", "").replace("http://", "").split("/")[0]
        
        # Check cache (वैकल्पिक - product और token कैश कर सकते हैं)
        cached_product = product_cache.get(domain) if domain in product_cache else None
        cached_token = token_cache.get(domain) if domain in token_cache else None
        
        # Call Shopify auto check function
        try:
            # Timeout के साथ कॉल करें
            result = await asyncio.wait_for(
                shopify_auto_check(
                    shopify_url=store_url,
                    card_num=card_info["number"],
                    card_mon=card_info["month"],
                    card_yer=card_info["year"],
                    card_cvc=card_info["cvv"],
                    proxy=proxy,
                    cached_product=cached_product,
                    cached_token=cached_token
                ),
                timeout=45  # 45 seconds timeout
            )
            
            status_message, proxy_alive = result
            
            # Extract price if available (status_message से निकालें)
            price = "0.00"
            import re
            price_match = re.search(r'\$(\d+\.?\d*)', status_message)
            if price_match:
                price = price_match.group(1)
            
            # Cache token/product if successful (for future use)
            if "APPROVED" in status_message or "CCN LIVE" in status_message:
                # You can implement caching logic here
                pass
            
            # Prepare response
            response_data = {
                "Response": status_message,
                "CC": cc_string,
                "Price": price,
                "Gate": cached_token or "live_check",
                "Site": store_url,
                "Proxy_Alive": proxy_alive
            }
            
            return JSONResponse(content=response_data)
            
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=408,
                content={
                    "Response": "Error: Request timeout (45s)",
                    "CC": cc_string,
                    "Price": "0",
                    "Gate": "timeout",
                    "Site": store_url,
                    "Proxy_Alive": "No"
                }
            )
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={
                    "Response": f"Error: {str(e)[:100]}",
                    "CC": cc_string,
                    "Price": "0",
                    "Gate": "internal_error",
                    "Site": store_url,
                    "Proxy_Alive": "No"
                }
            )
            
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "Response": f"Server Error: {str(e)[:50]}",
                "CC": "",
                "Price": "0",
                "Gate": "server_error",
                "Site": "",
                "Proxy_Alive": "No"
            }
        )

# Railway के लिए अतिरिक्त एंडपॉइंट
@app.get("/batch")
async def batch_check(request: Request):
    """Batch check multiple cards (POST request with JSON)"""
    if request.method == "POST":
        data = await request.json()
        cards = data.get("cards", [])
        url = data.get("url")
        proxy = data.get("proxy")
        
        if not cards or not url:
            return {"error": "Missing cards or url"}
        
        results = []
        for card in cards[:10]:  # Limit to 10 cards per batch
            result = await check_card_logic(card, url, proxy)
            results.append(result)
            await asyncio.sleep(0.5)  # Rate limiting
            
        return {"results": results}
    
    return {"error": "Use POST method with JSON"}

# Railway.app के लिए रन कॉन्फ़िगरेशन
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)