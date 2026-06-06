# Google Cloud Document AI Configuration
# Update these values with your actual Google Cloud project details

# Your Google Cloud Project ID
PROJECT_ID = "ai-calculator-459823"

# Document AI processor location (us, eu, asia1, etc.)
LOCATION = "us"

# Document AI processor ID - you'll get this after creating a processor
# For now, we'll create one programmatically
PROCESSOR_ID = "471771727a6244fe"

# Path to your service account JSON file (optional)
# If None, will use default credentials (gcloud auth application-default login)
CREDENTIALS_PATH = None

# Document AI processor types you can use:
PROCESSOR_TYPES = {
    "OCR_PROCESSOR": "Basic text extraction from images",
    "FORM_PARSER_PROCESSOR": "Extract forms, tables, and structured data",
    "DOCUMENT_OCR_PROCESSOR": "Advanced OCR with layout analysis",
    "INVOICE_PROCESSOR": "Extract invoice data",
    "EXPENSE_PROCESSOR": "Extract expense receipt data",
    "ID_DOCUMENT_PROCESSOR": "Extract ID document information"
}

# Default processor type for document processing
DEFAULT_PROCESSOR_TYPE = "OCR_PROCESSOR" 