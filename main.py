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
from google.cloud import firestore as google_firestore

# Configure logging for the application
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize Flask app
app = Flask(__name__)

# --- Firestore Connection Setup ---
db = None
try:
    # Attempt to connect to Firestore. This approach works for both
    # Cloud Run (via ApplicationDefault credentials) and local development
    # (via the GOOGLE_APPLICATION_CREDENTIALS environment variable).
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    logging.info("Firestore connected successfully.")
except Exception as e:
    logging.error(f"Error initializing Firebase: {e}. Running in mock data mode.")

# --- Mock Data for Local Development/Demonstration ---
# This data is used if no Firestore connection is established.
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

# Mock data for insurance cost calculation
INSURANCE_RATES = {
    "MedStar Health": {
        "appointment_cost": 150.00,
        "co_pay": 25.00
    },
    "Blue Cross Blue Shield": {
        "appointment_cost": 180.00,
        "co_pay": 30.00
    },
    "default": {
        "appointment_cost": 200.00,
        "co_pay": 50.00
    }
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
    """
    Sends a confirmation email using a secure SMTP connection.
    Environment variables for SMTP must be set for this to work.
    """
    smtp_server = os.environ.get('SMTP_SERVER')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    sender_email = os.environ.get('SMTP_EMAIL')
    password = os.environ.get('SMTP_PASSWORD')

    # IMPORTANT: Check if credentials are set before attempting to send.
    if not all([smtp_server, sender_email, password]):
        logging.warning("SMTP configuration not set via environment variables. Skipping email.")
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
    
    Booking Details:
    - Doctor: {booking_details.get('doctorName')}
    - Date: {booking_details.get('appointmentDate')}
    - Time: {booking_details.get('appointmentTime')}
    
    Cost Breakdown:
    - Total Appointment Cost: ${total_cost:.2f}
    - Your Co-pay: ${patient_copay:.2f}
    
    We look forward to seeing you!
    Best regards,
    The Clinic Team
    """

    html_content = f"""
    <html>
    <body style="font-family: sans-serif; line-height: 1.6;">
        <h2>Appointment Confirmation</h2>
        <p>Hello,</p>
        <p>Your appointment has been successfully booked with <b>{booking_details.get('doctorName')}</b> on <b>{booking_details.get('appointmentDate')}</b> at <b>{booking_details.get('appointmentTime')}</b>.</p>
        <h3>Booking Details:</h3>
        <ul>
            <li><b>Doctor:</b> {booking_details.get('doctorName')}</li>
            <li><b>Date:</b> {booking_details.get('appointmentDate')}</li>
            <li><b>Time:</b> {booking_details.get('appointmentTime')}</li>
        </ul>
        <h3>Cost Breakdown:</h3>
        <ul>
            <li><b>Total Appointment Cost:</b> ${total_cost:.2f}</li>
            <li><b>Your Co-pay (After Insurance Claim):</b> ${patient_copay:.2f}</li>
        </ul>
        <p>We look forward to seeing you!</p>
        <p>Best regards,<br>The Clinic Team</p>
    </body>
    </html>
    """

    part1 = MIMEText(text_content, "plain")
    part2 = MIMEText(html_content, "html")
    msg.attach(part1)
    msg.attach(part2)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls(context=context)
            server.login(sender_email, password)
            server.sendmail(sender_email, email, msg.as_string())
        logging.info(f"Email sent successfully via SMTP to {email}")
    except Exception as e:
        logging.error(f"Failed to send email via SMTP: {e}")

def find_available_doctors(specialty, location, date_str):
    """
    Searches for available doctors by specialty, location, and date.
    Returns a list of doctors with times available on the given date.
    """
    available_doctors = []
    if db:
        docs_ref = db.collection('doctors')
        docs = docs_ref.where(filter=firestore.FieldFilter('specialty', '==', specialty)).where(filter=firestore.FieldFilter('city', '==', location)).stream()
        for doc in docs:
            doctor_data = doc.to_dict()
            availability_map = doctor_data.get('availability', {})
            if date_str in availability_map and isinstance(availability_map[date_str], list) and availability_map[date_str]:
                available_doctors.append(doctor_data)
    else: # Mock data fallback
        for doc in MOCK_DOCTORS.values():
            if doc.get('specialty') == specialty and doc.get('city') == location:
                availability_map = doc.get('availability', {})
                if date_str in availability_map and isinstance(availability_map[date_str], list) and availability_map[date_str]:
                    available_doctors.append(doc)
    return available_doctors

# --- Webhook Endpoints ---
@app.route('/')
def home():
    """Returns a simple message to confirm the service is running."""
    return "Webhook is running successfully!"

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Dialogflow CX webhook for doctor availability and booking.
    """
    logging.info("--- Webhook Request Received ---")
    request_data = request.get_json()
    logging.info(f"Full Request JSON: {request_data}")
    
    session_info = request_data.get('sessionInfo', {})
    parameters = session_info.get('parameters', {})
    tag = request_data.get('fulfillmentInfo', {}).get('tag')
    
    response_text = "I'm sorry, an error occurred. Please try again."
    
    if tag == 'search_doctors':
        specialty = parameters.get('specialty')
        location = parameters.get('location', {}).get('city', parameters.get('location'))
        date_param = parameters.get('date')
        
        if not specialty or not location:
            response_text = "I'm missing some information. Please provide your preferred specialty and location."
        else:
            try:
                requested_date_obj = datetime.fromisoformat(date_param.replace('Z', '+00:00')).date() if date_param else datetime.now(pytz.utc).date()
                date_str = requested_date_obj.strftime('%Y-%m-%d')
                
                today = datetime.now(pytz.utc).date()
                if requested_date_obj < today:
                    response_text = "I can only check for future appointments. Please provide a date that isn't in the past."
                else:
                    available_doctors = find_available_doctors(specialty, location, date_str)
                    
                    if not available_doctors:
                        found_next_date = False
                        for i in range(1, 8):
                            next_date = requested_date_obj + timedelta(days=i)
                            next_date_str = next_date.strftime('%Y-%m-%d')
                            available_doctors = find_available_doctors(specialty, location, next_date_str)
                            if available_doctors:
                                response_parts = [f"I couldn't find any appointments for your requested date. However, I found some for the next available date, which is {next_date.strftime('%B %d, %Y')}."]
                                for i, doc in enumerate(available_doctors):
                                    times = ", ".join(doc['availability'][next_date_str])
                                    response_parts.append(f"{i+1}. {doc['name']}, {doc['specialty']} with times: {times}")
                                response_text = "\n".join(response_parts)
                                found_next_date = True
                                break
                        if not found_next_date:
                            response_text = f"I could not find any {specialty} doctors in {location} available on or after {requested_date_obj.strftime('%B %d, %Y')}. Would you like to check a different date or location?"
                    else:
                        response_parts = []
                        for i, doc in enumerate(available_doctors):
                            times = ", ".join(doc['availability'][date_str])
                            response_parts.append(f"{i+1}. {doc['name']}, {doc['specialty']} with times: {times}")
                        response_text = "Here are the available doctors and their times:\n" + "\n".join(response_parts)

            except Exception as e:
                logging.error(f"Error searching for doctors: {e}\n{traceback.format_exc()}")
                response_text = "I am having trouble looking for doctors right now. Please try again later."
    
    elif tag == 'collect_patient_info':
        try:
            doctor_name_param = parameters.get('doctor_name')
            appointment_time_param = parameters.get('appointment_time')
            appointment_date_param = parameters.get('appointment_date')
            
            # Robustly get doctor name as a string
            doctor_name = doctor_name_param.get('original') if isinstance(doctor_name_param, dict) else doctor_name_param
            
            if not all([doctor_name, appointment_time_param, appointment_date_param]):
                response_text = "I seem to have lost the appointment details. Please try selecting a time again."
            else:
                appointment_time = f"{appointment_time_param.get('hours', 0):02}:{appointment_time_param.get('minutes', 0):02}"
                appointment_date = f"{appointment_date_param.get('year', 0)}-{appointment_date_param.get('month', 0)}-{appointment_date_param.get('day', 0)}"

                custom_payload = {
                    "doctorName": doctor_name,
                    "appointmentTime": appointment_time,
                    "appointmentDate": appointment_date
                }
                
                response_text = f"You're booking with {doctor_name} on {appointment_date} at {appointment_time}. Please provide your personal and insurance details to complete the booking."
                return jsonify({
                    'fulfillmentResponse': {
                        'messages': [
                            {'text': {'text': [response_text]}},
                            {'payload': {'customPayload': custom_payload}}
                        ]
                    }
                })
        except Exception as e:
            logging.error(f"Error in collect_patient_info: {e}\n{traceback.format_exc()}")
            response_text = "I am having trouble with the next step. Please try again later."

    elif tag == 'ConfirmCost':
        try:
            insurance_provider = parameters.get('insuranceprovider')
            if not insurance_provider:
                response_text = "I'm missing your insurance provider information to calculate the cost. Please provide it."
            else:
                cost_details = calculate_appointment_cost(insurance_provider)
                total_cost = cost_details['totalCost']
                patient_copay = cost_details['patientCopay']
                response_text = f"The total cost for this appointment is ${total_cost:.2f} with a patient co-pay of ${patient_copay:.2f}. Would you like to confirm this booking?"
        except Exception as e:
            logging.error(f"Error during ConfirmCost process: {e}\n{traceback.format_exc()}")
            response_text = "I am having trouble calculating the cost right now. Please try again later."

    elif tag == 'book_appointment':
        try:
            patient_full_name = parameters.get('name', {}).get('original')
            dob_param = parameters.get('dateofbirth')
            insurance_provider = parameters.get('insuranceprovider')
            policy_number = parameters.get('policynumber')
            specialty = parameters.get('specialty')
            doctor_name = parameters.get('doctor_name', {}).get('original')
            appointment_date_param = parameters.get('appointment_date')
            appointment_time_param = parameters.get('appointment_time')

            if not all([patient_full_name, dob_param, insurance_provider, policy_number, specialty, doctor_name, appointment_date_param, appointment_time_param]):
                response_text = "I'm missing some information to complete your booking. Please provide all details."
            else:
                appointment_date = f"{appointment_date_param['year']}-{appointment_date_param['month']}-{appointment_date_param['day']}"
                appointment_time = f"{appointment_time_param['hours']}:{appointment_time_param['minutes']}"
                
                name_parts = patient_full_name.split(' ', 1)
                first_name = name_parts[0]
                last_name = name_parts[1] if len(name_parts) > 1 else ''

                if db:
                    # Use a transaction to ensure a single, atomic update.
                    @google_firestore.transactional
                    def update_patient_and_doctor_in_transaction(transaction, patient_ref, doctor_ref, booking_info):
                        patient_snapshot = patient_ref.get(transaction=transaction)
                        if not patient_snapshot.exists:
                            raise ValueError("Patient not found.")
                        
                        doctor_snapshot = doctor_ref.get(transaction=transaction)
                        if not doctor_snapshot.exists:
                            raise ValueError("Doctor not found.")

                        doctor_data_trans = doctor_snapshot.to_dict()
                        availability_list_trans = doctor_data_trans.get('availability', {}).get(appointment_date, [])
                        
                        if booking_info['appointmentTime'] not in availability_list_trans:
                            raise ValueError("Time slot is no longer available.")
                        
                        # Atomically update both documents
                        current_bookings = patient_snapshot.to_dict().get('bookings', [])
                        current_bookings.append(booking_info)
                        transaction.update(patient_ref, {'bookings': current_bookings})
                        
                        availability_list_trans.remove(booking_info['appointmentTime'])
                        update_path = f"availability.{appointment_date}"
                        transaction.update(doctor_ref, {update_path: availability_list_trans})
                    
                    try:
                        patients_ref = db.collection('patients')
                        patient_query = patients_ref.where(filter=google_firestore.FieldFilter('name', '==', first_name)).where(filter=google_firestore.FieldFilter('surname', '==', last_name)).limit(1).stream()
                        patient_doc = next(patient_query, None)
                        if not patient_doc:
                            response_text = "I could not find a patient with the provided details. Please try again."
                        else:
                            doctor_query = db.collection('doctors').where(filter=google_firestore.FieldFilter('name', '==', doctor_name)).limit(1).stream()
                            doctor_doc = next(doctor_query, None)
                            if not doctor_doc:
                                response_text = "The doctor you selected could not be found."
                            else:
                                cost_details = calculate_appointment_cost(insurance_provider)
                                booking_details = {
                                    "bookingId": str(uuid.uuid4()),
                                    "bookingType": "appointment",
                                    "doctorName": doctor_name,
                                    "specialty": specialty,
                                    "appointmentDate": appointment_date,
                                    "appointmentTime": appointment_time,
                                    "costBreakdown": cost_details,
                                    "status": "confirmed",
                                    "createdAt": firestore.SERVER_TIMESTAMP
                                }
                                
                                update_patient_and_doctor_in_transaction(db.transaction(), patient_doc.reference, doctor_doc.reference, booking_details)
                                
                                patient_email = patient_doc.to_dict().get('email')
                                if patient_email:
                                    send_email_to_patient(patient_email, booking_details)
                                
                                response_text = f"Success! Your booking has been confirmed with {doctor_name}. An email has been sent to your registered address."
                    except ValueError as ve:
                        logging.error(f"Booking transaction failed due to validation error: {ve}")
                        response_text = str(ve)
                    except Exception as e:
                        logging.error(f"Transaction failed: {e}\n{traceback.format_exc()}")
                        response_text = "An unexpected error occurred during the booking process. Please try again."
                else:
                    # Mock data booking logic
                    patient_data = MOCK_PATIENTS.get(patient_full_name)
                    doctor_data_list = [d for d in MOCK_DOCTORS.values() if d['name'] == doctor_name]
                    if patient_data and doctor_data_list:
                        doctor_data = doctor_data_list[0]
                        if appointment_time in doctor_data['availability'].get(appointment_date, []):
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
                            
                            doctor_data['availability'][appointment_date].remove(appointment_time)
                            patient_data['bookings'].append(booking_details)
                            send_email_to_patient(patient_data['email'], booking_details)
                            response_text = f"Success! Your booking has been confirmed with {doctor_name}. An email has been sent to your registered address."
                        else:
                            response_text = "I'm sorry, that time slot is no longer available. Please select a different time."
                    else:
                        response_text = "I could not complete the booking. Either the patient or doctor was not found, or the time slot is unavailable."
        except Exception as e:
            logging.error(f"Error during booking process: {e}\n{traceback.format_exc()}")
            response_text = "I am having trouble processing your booking right now. Please try again later."
    
    logging.info(f"Sending response to Dialogflow: {response_text}")
    return jsonify({
        'fulfillmentResponse': {
            'messages': [
                {
                    'text': {
                        'text': [response_text]
                    }
                }
            ]
        }
    })

# --- Application Entry Point ---
if __name__ == '__main__':
    logging.info("Starting application locally...")
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
