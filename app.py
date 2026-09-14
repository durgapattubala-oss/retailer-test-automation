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

# Directory for storing test plans
TEST_PLANS_DIR = "test_plans"
os.makedirs(TEST_PLANS_DIR, exist_ok=True)

# Retrieve API Key safely
api_key = st.secrets.get("GEMINI_API_KEY")
if not api_key:
    st.error("⚠️ GEMINI_API_KEY is missing from Secrets! Please configure it in Streamlit Cloud settings.")
    st.stop()

# Clean API Key
api_key = api_key.strip().strip('"').strip("'")
client = genai.Client(api_key=api_key)

# Function to get available test plans from the local directory
def get_available_test_plans():
    files = [f for f in os.listdir(TEST_PLANS_DIR) if f.endswith(".pdf")]
    # Standardize display names from filenames (e.g., Best_Buy_US_Dropship.pdf -> Best Buy US Dropship)
    return sorted(files)

# Sidebar Configurations
st.sidebar.header("Test Configuration")

# Get list of existing PDF files in `./test_plans/`
available_files = get_available_test_plans()

if not available_files:
    st.sidebar.warning("⚠️ No test plans found in `./test_plans/`. Please upload one below.")
    selected_file = None
else:
    selected_file = st.sidebar.selectbox(
        "Select Target Retailer Test Plan",
        available_files,
        format_func=lambda x: x.replace(".pdf", "").replace("_", " ")
    )

override_tracking = st.sidebar.text_input("Tracking Number Override", value="1Z0000000000000000")

# ➕ UI Section: Add / Upload New Retailer Test Plan
with st.sidebar.expander("➕ Add New Retailer Test Plan"):
    new_retailer_name = st.text_input("Retailer Name (e.g., Target US Dropship)")
    uploaded_plan_pdf = st.file_uploader("Upload Test Plan PDF", type=["pdf"], key="new_plan_uploader")
    
    if st.button("Save Test Plan"):
        if new_retailer_name and uploaded_plan_pdf:
            # Format filename safely
            formatted_name = new_retailer_name.strip().replace(" ", "_") + ".pdf"
            save_path = os.path.join(TEST_PLANS_DIR, formatted_name)
            
            with open(save_path, "wb") as f:
                f.write(uploaded_plan_pdf.getvalue())
            
            st.success(f"✅ Saved `{formatted_name}`! Refreshing menu...")
            st.rerun()
        else:
            st.error("Please provide both a name and a PDF file.")

# Main Screen: Single File Uploader for Order Screenshot
st.markdown("### Step 1: Upload Order Screenshot")
order_pdf_file = st.file_uploader("Upload Order Screenshot PDF", type=["pdf"])

def pdf_to_all_page_parts(pdf_bytes, label="Doc"):
    """Converts ALL pages of a PDF into image Parts for Gemini Vision."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        
        part = types.Part.from_bytes(
            data=img_byte_arr.getvalue(), 
            mime_type="image/png"
        )
        parts.append(part)
    return parts

def get_est_date():
    """Returns current date in Eastern Standard Time (EST/EDT)."""
    est = pytz.timezone('America/New_York')
    return datetime.datetime.now(est).strftime("%Y-%m-%d")

# Processing Block
if order_pdf_file and selected_file:
    if st.button("⚡ Run Automation Engine"):
        with st.spinner("Analyzing Order Screenshot against Selected Retailer Test Plan..."):
            try:
                # Read selected preloaded test plan PDF from disk
                testplan_path = os.path.join(TEST_PLANS_DIR, selected_file)
                with open(testplan_path, "rb") as f:
                    testplan_bytes = f.read()

                # Convert pages to image parts
                order_parts = pdf_to_all_page_parts(order_pdf_file.getvalue(), label="Order")
                testplan_parts = pdf_to_all_page_parts(testplan_bytes, label="TestPlan")

                retailer_display_name = selected_file.replace(".pdf", "").replace("_", " ")

                prompt = f"""
                You are an expert retail order testing parser.
                The provided images consist of:
                1. Order Screenshot PDF pages containing Purchase Orders (POs), Vendor SKUs, Ship-To Names, and Order Quantities.
                2. Retailer Test Plan PDF pages containing test case instructions, expected ship quantities, and cancellation requirements.

                Target Retailer Scope: {retailer_display_name}

                Task & Matching Rules:
                1. Read every page of the Order Screenshot to capture ALL PO line items.
                2. Match each line item to the Retailer Test Plan using the 'Ship-To Name', SKU, or Test Scenario sequence.
                3. Calculate the exact:
                   - ship_quantity: How many units to fulfill/ship according to the test plan instructions.
                   - cancel_quantity: How many units to cancel according to the test plan instructions.
                
                If a test plan mandates a full cancellation, ship_quantity should be 0 and cancel_quantity equal to the ordered quantity.
                If partial shipping is mandated, split the quantities accordingly.

                Return ONLY a valid JSON array matching this exact schema:
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

                # Combine payload
                contents_payload = []
                contents_payload.extend(order_parts)
                contents_payload.extend(testplan_parts)
                contents_payload.append(prompt)

                response = client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=contents_payload,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )

                processed_data = json.loads(response.text)

            except Exception as err:
                st.error(f"❌ Automation Processing Error: {err}")
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

            # 3. Cancellation (Reason Code set to 1)
            if cancel_qty > 0:
                cancellation_rows.append({
                    "Invoice ID": po_num,
                    "SKU": sku,
                    "Quantity": cancel_qty,
                    "Cancel Reason Code": 1,
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
elif not selected_file:
    st.info("💡 Please upload or add a retailer test plan to get started.")
