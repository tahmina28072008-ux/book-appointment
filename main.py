import os
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import traceback
import uuid
import pytz

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
            logging.warning("No GOOGLE_APPLICATION_CREDENTIALS found. Using mock data mode.")
    except Exception as e:
        logging.error(f"Error initializing Firebase: {e}")
        logging.warning("Continuing without database connection. Using mock data.")

# --- Mock Data ---
tomorrow = datetime.now().date() + timedelta(days=1)

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
        'city': 'London',
        'availability': {
            (tomorrow + timedelta(days=i)).strftime('%Y-%m-%d'): ["09:00 AM", "10:00 AM", "11:00 AM", "02:00 PM"]
            for i in range(5)
        }
    },
    'gp-002': {
        'id': 'gp-002',
        'name': 'Dr. Adam Collins, MRCGP',
        'specialty': 'General Practitioner',
        'city': 'London',
        'availability': {
            (tomorrow + timedelta(days=i)).strftime('%Y-%m-%d'): ["09:00 AM", "10:00 AM", "11:00 AM", "05:00 PM"]
            for i in range(5)
        }
    }
}

INSURANCE_RATES = {
    "MedStar Health": {"appointment_cost": 150.00, "co_pay": 25.00},
    "Blue Cross Blue Shield": {"appointment_cost": 180.00, "co_pay": 30.00},
    "default": {"appointment_cost": 200.00, "co_pay": 50.00}
}

# --- Helper Functions ---
def calculate_appointment_cost(insurance_provider: str) -> dict:
    rates = INSURANCE_RATES.get(insurance_provider, INSURANCE_RATES["default"])
    return {
        "totalCost": rates["appointment_cost"],
        "patientCopay": rates["co_pay"],
        "insuranceClaim": rates["appointment_cost"] - rates["co_pay"]
    }

def send_email_to_patient(email: str, patient_name: str, booking_details: dict):
    smtp_server = os.environ.get('SMTP_SERVER', "smtp.gmail.com")
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    sender_email = os.environ.get('SMTP_EMAIL', "niljoshna28@gmail.com")
    password = os.environ.get('SMTP_PASSWORD', "nxlcscihekyxcedc")

    if sender_email == "your_email@gmail.com" or password == "your_app_password":
        logging.warning("SMTP configuration not set. Cannot send email.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Appointment Confirmation"
    msg["From"] = sender_email
    msg["To"] = email

    cost_breakdown = booking_details.get('costBreakdown', {})
    total_cost = cost_breakdown.get('totalCost', 0)
    patient_copay = cost_breakdown.get('patientCopay', 0)

    # Plain text
    text_content = f"""
Hello {patient_name},

Your appointment has been successfully booked!

Doctor: {booking_details.get('doctorName')}
Specialty: {booking_details.get('specialty')}
Date: {booking_details.get('appointmentDate')}
Time: {booking_details.get('appointmentTime')}

Total Cost: ${total_cost:.2f}
Patient Co-pay: ${patient_copay:.2f}

Thank you for using our service!
"""

    # HTML email
    html_content = f"""
<html><body>
<h2>Hello {patient_name},</h2>
<p>Your appointment has been successfully booked!</p>
<table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse;">
<tr><th>Doctor</th><td>{booking_details.get('doctorName')}</td></tr>
<tr><th>Specialty</th><td>{booking_details.get('specialty')}</td></tr>
<tr><th>Date</th><td>{booking_details.get('appointmentDate')}</td></tr>
<tr><th>Time</th><td>{booking_details.get('appointmentTime')}</td></tr>
<tr><th>Total Cost</th><td>${total_cost:.2f}</td></tr>
<tr><th>Patient Co-pay</th><td>${patient_copay:.2f}</td></tr>
</table>
<p>Thank you for using our service!</p>
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
            logging.info(f"Email sent successfully to {email}")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

def find_available_doctors(specialty, location):
    available_doctors = []
    start_date = datetime.now().date() + timedelta(days=1)
    for doc in MOCK_DOCTORS.values():
        if doc['specialty'].lower() == specialty.lower() and doc['city'].lower() == location.lower():
            availability_map = {d: times for d, times in doc['availability'].items() if datetime.strptime(d, "%Y-%m-%d").date() >= start_date and times}
            if availability_map:
                doc['availability'] = availability_map
                available_doctors.append(doc)
    return available_doctors

# --- Webhook ---
@app.route('/webhook', methods=['POST'])
def webhook():
    request_data = request.get_json()
    session_info = request_data.get('sessionInfo', {})
    parameters = session_info.get('parameters', {})
    tag = request_data.get('fulfillmentInfo', {}).get('tag')

    response_text = "I'm sorry, an error occurred."

    if tag == 'search_doctors':
        specialty = parameters.get('specialty')
        location_param = parameters.get('location')
        location = location_param.get('city') if isinstance(location_param, dict) else location_param

        if not specialty or not location:
            response_text = "Please provide both specialty and location."
        else:
            doctors = find_available_doctors(specialty, location)
            if doctors:
                response_text_list = ["I found the following doctors with availability:"]
                for doc in doctors:
                    response_text_list.append(f"\nDoctor: {doc['name']} ({doc['specialty']})")
                    for date, times in sorted(doc['availability'].items()):
                        times_str = ', '.join(times)
                        response_text_list.append(f"  {date}: {times_str}")
                response_text = "\n".join(response_text_list)
            else:
                response_text = f"No {specialty} doctors available in {location} from tomorrow onward."

    elif tag == 'collect_patient_info':
        patient_name = parameters.get('name')
        patient_full_name = patient_name.get('original') if patient_name else None
        patient_surname = parameters.get('surname')
        patient_dob = parameters.get('date-of-birth')
        patient_email = parameters.get('email')

        if not patient_full_name or not patient_surname or not patient_dob or not patient_email:
            response_text = "I'm missing some patient information. Please provide your full name, date of birth, and email."
        else:
            response_text = f"Thank you, {patient_full_name}. I have collected your information. What is your insurance provider? We will use this to estimate the appointment cost."

    elif tag == 'ConfirmCost':
        insurance_provider = parameters.get('insuranceprovider')
        if not insurance_provider:
            response_text = "I'm sorry, I couldn't find your insurance provider. Could you please provide it again?"
        else:
            cost_details = calculate_appointment_cost(insurance_provider)
            total_cost = cost_details.get('totalCost', 0)
            patient_copay = cost_details.get('patientCopay', 0)
            insurance_claim = cost_details.get('insuranceClaim', 0)
            
            response_text = (
                f"Based on your insurance, the estimated cost of your appointment is:\n"
                f"Total Cost: ${total_cost:.2f}\n"
                f"Patient Co-pay: ${patient_copay:.2f}\n"
                f"Insurance Claim: ${insurance_claim:.2f}\n"
                "Do you want to proceed with this booking?"
            )

    elif tag == 'book_appointment':
        patient_name_param = parameters.get('name')
        patient_full_name = patient_name_param['original'] if patient_name_param else None
        patient_data = MOCK_PATIENTS.get(patient_full_name)
        doctor_name = parameters.get('doctor_name')['original']
        appointment_date_param = parameters.get('appointment_date')
        appointment_time_param = parameters.get('appointment_time')
        appointment_date = f"{appointment_date_param['year']}-{appointment_date_param['month']}-{appointment_date_param['day']}"
        appointment_time = f"{appointment_time_param['hours']}:{appointment_time_param['minutes']}"
        specialty = parameters.get('specialty')
        insurance_provider = parameters.get('insuranceprovider')

        doctor_data = next((doc for doc in MOCK_DOCTORS.values() if doc['name'] == doctor_name), None)
        if doctor_data and patient_data:
            if appointment_time in doctor_data['availability'].get(appointment_date, []):
                doctor_data['availability'][appointment_date].remove(appointment_time)
                cost_details = calculate_appointment_cost(insurance_provider)
                booking_details = {
                    "bookingId": str(uuid.uuid4()),
                    "bookingType": "appointment",
                    "doctorName": doctor_name,
                    "specialty": specialty,
                    "appointmentDate": appointment_date,
                    "appointmentTime": appointment_time,
                    "costBreakdown": cost_details,
                    "status": "confirmed"
                }
                patient_data['bookings'].append(booking_details)
                send_email_to_patient(patient_data['email'], patient_data['name'], booking_details)
                response_text = f"Success! Your booking with {doctor_name} on {appointment_date} at {appointment_time} is confirmed. An email has been sent to {patient_data['email']}."
            else:
                response_text = "Selected time slot is not available. Please choose another time."

    return jsonify({'fulfillmentResponse': {'messages': [{'text': {'text': [response_text]}}]}})

@app.route('/')
def home():
    return "Webhook is running successfully!"

if __name__ == '__main__':
    logging.info("Starting application locally...")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
