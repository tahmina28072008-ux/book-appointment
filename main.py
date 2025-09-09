import os
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta, timezone
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import traceback
import uuid
import pytz
from google.cloud import firestore as google_firestore

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize Flask app
app = Flask(__name__)

# --- Firestore Connection Setup ---
db = None
try:
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)
    logging.info("Firestore connected using Cloud Run environment credentials.")
    db = firestore.client()
except ValueError:
    try:
        if os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
            cred = credentials.Certificate(os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'))
            firebase_admin.initialize_app(cred)
            logging.info("Firestore connected using GOOGLE_APPLICATION_CREDENTIALS.")
            db = firestore.client()
        else:
            logging.warning("No GOOGLE_APPLICATION_CREDENTIALS found. Running in mock data mode.")
    except Exception as e:
        logging.error(f"Error initializing Firebase: {e}")
        logging.warning("Continuing without database connection. Using mock data.")

# Mock data for demonstration if Firebase is not connected
MOCK_PATIENTS = {
    'Tahmina Akhtar': {
        'name': 'Tahmina',
        'surname': 'Akhtar',
        'dateOfBirth': '1992-03-12',
        'insuranceProvider': 'MedStar Health',
        'policyNumber': 'D123456',
        'email': 'tahmina.akhtar2807@gmail.com',
        'bookings': [],
    }
}

MOCK_DOCTORS = {
    'gp-001': {
        'id': 'gp-001',
        'name': 'Dr. Lucy Morgan, MRCGP',
        'specialty': 'General Practitioner',
        'city': 'New York',
        'availability': {
            '2025-09-07': ["13:00", "14:00"]
        }
    },
    'gp-002': {
        'id': 'gp-002',
        'name': 'Dr. Adam Collins, MRCGP',
        'specialty': 'General Practitioner',
        'city': 'London',
        'availability': {
            '2025-09-07': [],
            '2025-09-08': ["09:00", "10:00", "11:00", "12:00", "13:00"]
        }
    }
}

INSURANCE_RATES = {
    "MedStar Health": {"appointment_cost": 150.00, "co_pay": 25.00},
    "Blue Cross Blue Shield": {"appointment_cost": 180.00, "co_pay": 30.00},
    "default": {"appointment_cost": 200.00, "co_pay": 50.00}
}

# --- Core Business Logic Functions ---
def calculate_appointment_cost(insurance_provider: str) -> dict:
    """
    Calculates the appointment cost and patient's co-pay based on insurance.
    """
    rates = INSURANCE_RATES.get(insurance_provider, INSURANCE_RATES["default"])
    total_cost = rates["appointment_cost"]
    co_pay = rates["co_pay"]
    
    return {
        "totalCost": total_cost,
        "patientCopay": co_pay,
        "insuranceClaim": total_cost - co_pay
    }


def send_email_to_patient(email: str, booking_details: dict):
    smtp_server = os.environ.get('SMTP_SERVER', "smtp.gmail.com")
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    sender_email = os.environ.get('SMTP_EMAIL', "niljoshna28@gmail.com")
    password = os.environ.get('SMTP_PASSWORD', "nxlcscihekyxcedc")

    if sender_email == "your_email@gmail.com" or password == "your_app_password":
        logging.warning("SMTP configuration not set via environment variables. Cannot send email.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Appointment Confirmation"
    msg["From"] = sender_email
    msg["To"] = email

    cost_breakdown = booking_details.get('costBreakdown', {})
    total_cost = cost_breakdown.get('totalCost', 0)
    patient_copay = cost_breakdown.get('patientCopay', 0)

    text_content = f"""
    Appointment Confirmation
    Hello,
    Your appointment has been successfully booked with {booking_details.get('doctorName')} on {booking_details.get('appointmentDate')} at {booking_details.get('appointmentTime')}.
    """
    html_content = f"""
    <html><body><h2>Appointment Confirmation</h2>
    <p>Your appointment has been successfully booked with <b>{booking_details.get('doctorName')}</b> on <b>{booking_details.get('appointmentDate')}</b> at <b>{booking_details.get('appointmentTime')}</b>.</p>
    </body></html>
    """

    msg.attach(MIMEText(text_content, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls(context=context)
            server.login(sender_email, password)
            server.sendmail(sender_email, email, msg.as_string())
            logging.info(f"Email sent successfully via SMTP to {email}")
    except Exception as e:
        logging.error(f"Failed to send email via SMTP: {e}")


def find_available_doctors(specialty, location, date_str=None):
    """
    Fetch doctors by specialty & location. Returns all availability.
    Doctors are sorted by earliest available date.
    """
    available_doctors = []
    if db:
        docs_ref = db.collection('doctors')
        docs = docs_ref.where(
            filter=firestore.FieldFilter('specialty', '==', specialty.title())
        ).where(
            filter=firestore.FieldFilter('city', '==', location.title())
        ).stream()

        for doc in docs:
            doctor_data = doc.to_dict()
            availability_map = doctor_data.get('availability', {})
            valid_dates = [datetime.strptime(d, "%Y-%m-%d").date() for d, times in availability_map.items() if times]
            if valid_dates:
                earliest_date = min(valid_dates)
                doctor_data['earliest_date'] = earliest_date
                available_doctors.append(doctor_data)
    else:
        for doc in MOCK_DOCTORS.values():
            if doc['specialty'].lower() == specialty.lower() and doc['city'].lower() == location.lower():
                availability_map = doc.get('availability', {})
                valid_dates = [datetime.strptime(d, "%Y-%m-%d").date() for d, times in availability_map.items() if times]
                if valid_dates:
                    earliest_date = min(valid_dates)
                    doc['earliest_date'] = earliest_date
                    available_doctors.append(doc)

    available_doctors.sort(key=lambda d: d.get('earliest_date'))
    return available_doctors


# --- Webhook Endpoints ---
@app.route('/')
def home():
    return "Webhook is running successfully!"

@app.route('/webhook', methods=['POST'])
def webhook():
    logging.info("--- Webhook Request Received ---")
    request_data = request.get_json()
    logging.info(f"Full Request JSON: {request_data}")

    session_info = request_data.get('sessionInfo', {})
    parameters = session_info.get('parameters', {})
    tag = request_data.get('fulfillmentInfo', {}).get('tag')

    response_text = "I'm sorry, an error occurred. Please try again."

    if tag == 'search_doctors':
        specialty = parameters.get('specialty')
        location_param = parameters.get('location')
        location = location_param.get('city') if isinstance(location_param, dict) else location_param

        if not specialty or not location:
            response_text = "I'm missing some information. Please provide your preferred specialty and location."
        else:
            try:
                available_doctors = find_available_doctors(specialty, location)
                if available_doctors:
                    response_text_list = ["Here are the doctors I found:"]
                    for i, doc in enumerate(available_doctors):
                        response_text_list.append(f"\n{i+1}. {doc['name']} ({doc['specialty']}, {doc['city']})")
                        for date, times in sorted(doc['availability'].items()):
                            if times:
                                response_text_list.append(f"    - {date}: {', '.join(times)}")
                    response_text = "\n".join(response_text_list)
                else:
                    response_text = f"I could not find any {specialty} doctors in {location} with availability."
            except Exception as e:
                logging.error(f"Error searching for doctors: {e}")
                logging.error(traceback.format_exc())
                response_text = "I am having trouble looking for doctors right now. Please try again later."

    # Other tags remain unchanged...

    logging.info(f"Sending response to Dialogflow: {response_text}")
    return jsonify({
        'fulfillmentResponse': {
            'messages': [{ 'text': { 'text': [response_text] } }]
        }
    })

if __name__ == '__main__':
    logging.info("Starting application locally...")
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
