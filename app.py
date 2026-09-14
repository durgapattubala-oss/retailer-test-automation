import os
import json
import datetime
import pytz
import pandas as pd
import streamlit as st
from PIL import Image
import fitz  # PyMuPDF for converting PDF pages to images
from google import genai
from google.genai import types

# Set up Page Config
st.set_page_config(page_title="Retailer Testing Automation Engine", layout="wide")
st.title("🛒 Retailer Order & Inventory Testing Automation")

# Initialize Gemini Client
client = genai.Client()

# Scope of Retailers
RETAILERS_SCOPE = [
    "Belk US Dropship", "Best Buy US Dropship", "BJ's US Wholesale Dropship",
    "Costco CA Dropship", "Costco US Dropship", "Home Depot US Dropship",
    "Home Depot CA Dropship", "JCPenney US Dropship", "Lowe's US Dropship",
    "Lowe's CA Dropship", "Macy's US Dropship", "QVC US Dropship",
    "Staples US Dropship", "Staples Quill US Dropship", "Staples Advantage US Dropship",
    "Staples CA Dropship"
]

# Sidebar Controls
st.sidebar.header("Test Configuration")
selected_retailer = st.sidebar.selectbox("Select Target Retailer Scope", RETAILERS_SCOPE)
override_tracking = st.sidebar.text_input("Tracking Number Override", value="1Z0000000000000000")

# Input File Uploaders
col_up1, col_up2 = st.columns(2)
with col_up1:
    pdf_file = st.file_uploader("1. Upload Order Screenshot PDF", type=["pdf"])
with col_up2:
    testplan_file = st.file_uploader("2. Upload Retailer Test Plan (CSV/Excel)", type=["csv", "xlsx"])

def pdf_to_images(pdf_bytes):
    """Converts uploaded PDF pages into PIL Images for Gemini Vision."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(img)
        break  # Process first page or loop as needed
    return images

def get_est_date():
    """Gets current date formatted in Eastern Standard Time (EST/EDT)."""
    est = pytz.timezone('America/New_York')
    return datetime.datetime.now(est).strftime("%Y-%m-%d")

if pdf_file and testplan_file:
    # Read Test Plan
    if testplan_file.name.endswith(".csv"):
        df_testplan = pd.read_csv(testplan_file)
    else:
        df_testplan = pd.read_excel(testplan_file)

    st.success(f"Loaded Test Plan with {len(df_testplan)} scenarios.")

    if st.button("⚡ Run Automation Engine"):
        with st.spinner("Parsing Order PDF via Vision AI..."):
            images = pdf_to_images(pdf_file.read())
            
            prompt = """
            You are an order data extraction system. Analyze the order screenshot image(s) and extract all order lines into a JSON object.
            
            For each line item visible, extract:
            - po_number: string (Purchase Order Number / Order ID)
            - vendor_sku: string (Vendor SKU / Item Number)
            - ship_to_name: string (Full recipient / Ship-To Customer Name)
            - order_quantity: integer (Quantity ordered)

            Return ONLY valid JSON matching this schema:
            [
              {
                "po_number": "PO123456",
                "vendor_sku": "SKU-ABC-1",
                "ship_to_name": "John Doe",
                "order_quantity": 2
              }
            ]
            """

            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[images[0], prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            
            try:
                extracted_orders = json.loads(response.text)
            except Exception as e:
                st.error("Error parsing visual PDF data. Ensure screenshot quality is clear.")
                st.stop()

        st.subheader("Extracted PDF Data Preview")
        st.dataframe(pd.DataFrame(extracted_orders), use_container_width=True)

        # Processing Business Logic
        product_rows = []
        tracking_rows = []
        cancellation_rows = []
        
        current_est_date = get_est_date()
        unique_skus = set()

        for order in extracted_orders:
            po_num = str(order.get("po_number", "")).strip()
            sku = str(order.get("vendor_sku", "")).strip()
            ship_to = str(order.get("ship_to_name", "")).strip().lower()
            order_qty = int(order.get("order_quantity", 0))

            # --- Rule 1: Product Upload Template Data ---
            if sku not in unique_skus:
                unique_skus.add(sku)
                product_rows.append({
                    "SKU": sku,
                    "Quantity": 50,
                    "Quantity Update Type": "Absolute"
                })

            # Match order with Test Plan by Ship-To Name
            # (Fuzzy or exact match against test plan column 'Ship-To Name' or 'ShipToName')
            tp_matches = df_testplan[
                df_testplan['Ship-To Name'].astype(str).str.strip().str.lower() == ship_to
            ] if 'Ship-To Name' in df_testplan.columns else pd.DataFrame()

            # Fallback if no direct match found: treat full order as shipped
            ship_qty = order_qty
            cancel_qty = 0

            if not tp_matches.empty:
                # Disambiguate if multiple rows match: match by SKU if available in test plan
                matched_row = tp_matches.iloc[0]
                if 'SKU' in tp_matches.columns:
                    sku_match = tp_matches[tp_matches['SKU'].astype(str).str.strip() == sku]
                    if not sku_match.empty:
                        matched_row = sku_match.iloc[0]

                # Extract instructions from Test Plan columns (assuming columns 'Ship Qty' and 'Cancel Qty')
                ship_qty = int(matched_row.get('Ship Qty', order_qty))
                cancel_qty = int(matched_row.get('Cancel Qty', 0))

            # --- Rule 2: Shipping Tracking Template Data ---
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

            # --- Rule 3: Cancellation Template Data ---
            if cancel_qty > 0:
                cancellation_rows.append({
                    "Invoice ID": po_num,
                    "SKU": sku,
                    "Quantity": cancel_qty,
                    "Adjustment": 0
                })

        # Convert to DataFrames
        df_product_out = pd.DataFrame(product_rows)
        df_tracking_out = pd.DataFrame(tracking_rows)
        df_cancel_out = pd.DataFrame(cancellation_rows)

        # Display Deliverables
        st.markdown("---")
        st.header("Generated Output Templates")
        
        col_out1, col_out2, col_out3 = st.columns(3)
        
        with col_out1:
            st.subheader("1. Product Upload")
            st.dataframe(df_product_out, use_container_width=True)
            st.download_button(
                "📥 Download Product Template CSV",
                df_product_out.to_csv(index=False).encode('utf-8'),
                "Product_Upload_Template.csv",
                "text/csv"
            )

        with col_out2:
            st.subheader("2. Shipping Tracking")
            st.dataframe(df_tracking_out, use_container_width=True)
            st.download_button(
                "📥 Download Tracking Template CSV",
                df_tracking_out.to_csv(index=False).encode('utf-8'),
                "Shipping_Tracking_Template.csv",
                "text/csv"
            )

        with col_out3:
            st.subheader("3. Cancellation")
            st.dataframe(df_cancel_out, use_container_width=True)
            st.download_button(
                "📥 Download Cancellation Template CSV",
                df_cancel_out.to_csv(index=False).encode('utf-8'),
                "Cancellation_Template.csv",
                "text/csv"
            )
