import os
import json
import io
import datetime
import pytz
import pandas as pd
import streamlit as st
from PIL import Image
import fitz  # PyMuPDF
from google import genai
from google.genai import types

# Page Config
st.set_page_config(page_title="Retailer Testing Automation Engine", layout="wide")
st.title("🛒 Retailer Order & Inventory Testing Automation")

# Retrieve API Key safely
api_key = st.secrets.get("GEMINI_API_KEY")
if not api_key:
    st.error("⚠️ GEMINI_API_KEY is missing from Secrets! Please configure it in Streamlit Cloud settings.")
    st.stop()

# Clean API Key of any accidental whitespace or quotes
api_key = api_key.strip().strip('"').strip("'")
client = genai.Client(api_key=api_key)

# Scope of Retailers
RETAILERS_SCOPE = [
    "Belk US Dropship", "Best Buy US Dropship", "BJ's US Wholesale Dropship",
    "Costco CA Dropship", "Costco US Dropship", "Home Depot US Dropship",
    "Home Depot CA Dropship", "JCPenney US Dropship", "Lowe's US Dropship",
    "Lowe's CA Dropship", "Macy's US Dropship", "QVC US Dropship",
    "Staples US Dropship", "Staples Quill US Dropship", "Staples Advantage US Dropship",
    "Staples CA Dropship"
]

# Sidebar Configurations
st.sidebar.header("Test Configuration")
selected_retailer = st.sidebar.selectbox("Select Target Retailer Scope", RETAILERS_SCOPE)
override_tracking = st.sidebar.text_input("Tracking Number Override", value="1Z0000000000000000")

# Input File Uploaders
col_up1, col_up2 = st.columns(2)
with col_up1:
    order_pdf_file = st.file_uploader("1. Upload Order Screenshot PDF", type=["pdf"])
with col_up2:
    testplan_pdf_file = st.file_uploader("2. Upload Retailer Test Plan PDF", type=["pdf"])

def pdf_to_image_bytes(pdf_bytes):
    """Converts page 1 of PDF to PNG Bytes (most reliable format for GenAI API)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    pix = page.get_pixmap(dpi=200)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    return img_byte_arr.getvalue()

def get_est_date():
    """Returns current date in Eastern Standard Time (EST/EDT)."""
    est = pytz.timezone('America/New_York')
    return datetime.datetime.now(est).strftime("%Y-%m-%d")

if order_pdf_file and testplan_pdf_file:
    if st.button("⚡ Run Automation Engine"):
        with st.spinner("Analyzing Order Screenshot & Test Plan PDFs..."):
            try:
                # Convert PDFs to PNG bytes
                order_img_bytes = pdf_to_image_bytes(order_pdf_file.getvalue())
                testplan_img_bytes = pdf_to_image_bytes(testplan_pdf_file.getvalue())

                # Prepare inline data parts
                part_order = types.Part.from_bytes(data=order_img_bytes, mime_type="image/png")
                part_testplan = types.Part.from_bytes(data=testplan_img_bytes, mime_type="image/png")

                prompt = f"""
                You are an expert retail order testing parser.
                Image 1 is an Order Screenshot PDF containing Purchase Orders (POs), Vendor SKUs, Ship-To Names, and Order Quantities.
                Image 2 is a Retailer Test Plan PDF containing test case instructions, shipping scenarios, and cancellation requirements.

                Target Retailer: {selected_retailer}

                Instructions:
                1. Extract all line items from Image 1 (Order Screenshot):
                   - po_number
                   - vendor_sku
                   - ship_to_name
                   - order_quantity
                
                2. Cross-reference each extracted order line with Image 2 (Retailer Test Plan) by matching the 'ship_to_name' and line item details.
                
                3. Determine:
                   - ship_quantity: The quantity to ship based on the Test Plan instructions.
                   - cancel_quantity: The quantity to cancel based on the Test Plan instructions.

                Return ONLY a valid JSON array matching this structure:
                [
                  {{
                    "po_number": "PO12345",
                    "vendor_sku": "SKU-ABC",
                    "ship_to_name": "John Doe",
                    "order_quantity": 5,
                    "ship_quantity": 5,
                    "cancel_quantity": 0
                  }}
                ]
                """

                # Send request using strict Part objects
                response = client.models.generate_content(
                    model='gemini-3.5-flash',
                    contents=[part_order, part_testplan, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )

                processed_data = json.loads(response.text)

            except Exception as err:
                st.error(f"❌ API Error Details: {err}")
                st.stop()

        st.subheader("Extracted & Matched Data Preview")
        st.dataframe(pd.DataFrame(processed_data), use_container_width=True)

        # Build Output Templates
        product_rows = []
        tracking_rows = []
        cancellation_rows = []
        
        current_est_date = get_est_date()
        unique_skus = set()

        for row in processed_data:
            po_num = str(row.get("po_number", "")).strip()
            sku = str(row.get("vendor_sku", "")).strip()
            ship_qty = int(row.get("ship_quantity", 0))
            cancel_qty = int(row.get("cancel_quantity", 0))

            # 1. Product Upload
            if sku not in unique_skus:
                unique_skus.add(sku)
                product_rows.append({
                    "SKU": sku,
                    "Quantity": 50,
                    "Quantity Update Type": "Absolute"
                })

            # 2. Shipping Tracking
            if ship_qty > 0:
                tracking_rows.append({
                    "Invoice Number": po_num,
                    "SKU": sku,
                    "Tracking Number": override_tracking,
                    "Quantity": ship_qty,
                    "Date Shipped": current_est_date,
                    "Shipping Carrier Code": "UPS",
                    "Shipping Class Code": "Ground"
                })

            # 3. Cancellation
            if cancel_qty > 0:
                cancellation_rows.append({
                    "Invoice ID": po_num,
                    "SKU": sku,
                    "Quantity": cancel_qty,
                    "Adjustment": 0
                })

        df_product_out = pd.DataFrame(product_rows)
        df_tracking_out = pd.DataFrame(tracking_rows)
        df_cancel_out = pd.DataFrame(cancellation_rows)

        # UI Deliverables
        st.markdown("---")
        st.header("Generated Output Templates")
        
        col_out1, col_out2, col_out3 = st.columns(3)
        
        with col_out1:
            st.subheader("1. Product Upload")
            st.dataframe(df_product_out, use_container_width=True)
            st.download_button(
                "📥 Download Product CSV",
                df_product_out.to_csv(index=False).encode('utf-8'),
                "Product_Upload_Template.csv",
                "text/csv"
            )

        with col_out2:
            st.subheader("2. Shipping Tracking")
            st.dataframe(df_tracking_out, use_container_width=True)
            st.download_button(
                "📥 Download Tracking CSV",
                df_tracking_out.to_csv(index=False).encode('utf-8'),
                "Shipping_Tracking_Template.csv",
                "text/csv"
            )

        with col_out3:
            st.subheader("3. Cancellation")
            st.dataframe(df_cancel_out, use_container_width=True)
            st.download_button(
                "📥 Download Cancellation CSV",
                df_cancel_out.to_csv(index=False).encode('utf-8'),
                "Cancellation_Template.csv",
                "text/csv"
            )
