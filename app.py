# -*- coding: utf-8 -*-
"""app.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1rRSegdTULlczjwu11IQzYDFAIJmlE8TF
"""

import os
import io
import logging
import tempfile
import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import plotly.express as px
import matplotlib.pyplot as plt
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image
from fairlearn.metrics import demographic_parity_difference, equalized_odds_difference
from sklearn.metrics import confusion_matrix, accuracy_score, precision_score, recall_score
from torchvision.models import DenseNet121_Weights
import re
from collections import Counter

# New required imports:
import albumentations
import einops

# ------------------------- Logging Configuration -------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------------- Page Configuration -------------------------
st.set_page_config(
    page_title="Gender Bias in Radiology",
    page_icon="🐍",  # Favicon: little snake
    layout="wide"
)

# ------------------------- THEME & STYLE FUNCTIONS -------------------------
def set_background():
    style = """
    <style>
    .stApp {
        background: linear-gradient(to bottom right, #ffffff, #e6f7ff);
        color: #000;
    }
    .stTitle {
        font-size: 36px !important;
        font-weight: bold;
        color: #000 !important;
    }
    </style>
    """
    st.markdown(style, unsafe_allow_html=True)

set_background()

def set_gradient_progress_bar():
    st.markdown(
        """
        <style>
        div[data-testid="stProgressBar"] > div[role="progressbar"] > div {
            background: linear-gradient(to right, #4facfe, #00f2fe);
        }
        </style>
        """,
        unsafe_allow_html=True
    )

set_gradient_progress_bar()

# ------------------------- GLOBAL SESSION STATE -------------------------
if "df" not in st.session_state:
    st.session_state.df = None
if "df_results" not in st.session_state:
    st.session_state.df_results = pd.DataFrame(columns=["Image_ID", "Gender", "Prediction", "Probability"])

# ------------------------- MODEL & HELPER FUNCTIONS -------------------------
@st.cache_resource(show_spinner=True)
def load_chexnet_model():
    model = models.densenet121(weights=DenseNet121_Weights.IMAGENET1K_V1)
    model.classifier = nn.Linear(1024, 2)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    model.eval()
    return model, device

try:
    chexnet_model, device = load_chexnet_model()
    st.success("✅ CheXNet Model Loaded Successfully!")
except Exception as e:
    logging.error("Error loading CheXNet model", exc_info=True)
    st.error(f"🚨 Error loading CheXNet model: {e}")

@st.cache_resource(show_spinner=True)
def load_chexagent_model():
    from transformers import AutoModelForCausalLM, AutoTokenizer
    model_name = "StanfordAIMI/CheXagent-2-3b"
    dtype = torch.bfloat16
    # Force CheXagent to load on CPU to help reduce memory issues.
    device_agent = "cpu"
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(model_name, device_map="cpu", trust_remote_code=True)
    model = model.to(dtype)
    model.eval()
    return model, tokenizer, device_agent

def chexagent_inference(image, prompt="Analyze the chest X‑ray image and return what you see along with the disease name detected."):
    try:
        # Save image temporarily.
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            image.save(tmp.name)
            tmp_path = tmp.name
        model_agent, tokenizer, device_agent = load_chexagent_model()
        query = tokenizer.from_list_format([{'image': tmp_path}, {'text': prompt}])
        conv = [
            {"from": "system", "value": "You are a helpful assistant."},
            {"from": "human", "value": query}
        ]
        input_ids = tokenizer.apply_chat_template(conv, add_generation_prompt=True, return_tensors="pt")
        output = model_agent.generate(
            input_ids.to(device_agent),
            do_sample=False,
            num_beams=1,
            temperature=1.0,
            top_p=1.0,
            use_cache=True,
            max_new_tokens=128  # Reduced for memory usage.
        )[0]
        response = tokenizer.decode(output[input_ids.size(1):-1])
        os.remove(tmp_path)
        return response
    except Exception as ex:
        logging.error("CheXagent inference error", exc_info=True)
        raise ex

def unify_gender_label(label):
    text = str(label).strip().lower()
    male_keywords = ["m", "male", "man", "masculin"]
    female_keywords = ["f", "female", "woman", "femme"]
    if any(kw in text for kw in male_keywords):
        return "M"
    if any(kw in text for kw in female_keywords):
        return "F"
    return "Unknown"

def unify_disease_label(label):
    text = str(label).strip().lower()
    no_disease_keywords = ["no finding", "none", "negative", "normal", "0", "false", "no disease"]
    if any(kw in text for kw in no_disease_keywords):
        return "no disease"
    return text

@st.cache_resource(show_spinner=True)
def preprocess_image(image):
    transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor()
    ])
    return transform(image).unsqueeze(0)

# ------------------------- STATIC CHATBOT -------------------------
PREDEFINED_ANSWERS = {
    "what is gender bias?": "Gender bias refers to unequal representation or treatment based on gender. In radiology, it may lead to misdiagnoses if training data is not balanced.",
    "how does gender bias affect radiology?": "Bias can result in inaccurate disease detection and unequal treatment recommendations.",
    "what are common mitigation techniques?": "Techniques include threshold adjustment, reweighing, adversarial debiasing, and post-processing calibration.",
    "which papers are cited?": (
        "Key papers:\n"
        "- [Mehrabi et al. (2021)](https://arxiv.org/abs/1908.09635)\n"
        "- [Obermeyer et al. (2019)](https://www.science.org/doi/10.1126/science.aax2342)\n"
        "- [Larrazabal et al. (2020)](https://www.nature.com/articles/s41467-020-19109-9)"
    ),
    "how can i improve model fairness?": "You can improve fairness by collecting diverse data, applying mitigation techniques, and monitoring performance across subgroups.",
    "default": "I'm sorry, I don't have an answer for that. Please ask another question related to gender bias in radiology."
}

def static_chatbot(user_input):
    user_input = user_input.lower().strip()
    for key in PREDEFINED_ANSWERS:
        if key in user_input:
            return PREDEFINED_ANSWERS[key]
    return PREDEFINED_ANSWERS["default"]

# ------------------------- PAGE FUNCTIONS -------------------------
def home_page():
    st.title("🏠 Home")
    st.markdown("## Importance of Gender Bias in AI")
    st.markdown(
        """
        **Why Gender Bias Matters:**

        - Ethical concerns: Unfair treatment may result.
        - Clinical impact: Risk of misdiagnosis.
        - Regulatory pressure: Fairness is required.
        - Research evidence: Underrepresentation leads to poorer outcomes.

        This project, designed for WiDS Datathon 2025, explores responsible AI in radiology.
        """
    )
    st.info("Use the sidebar to navigate through the app.")
    st.markdown("---")
    st.markdown("### Thank You")
    st.markdown(
        """
        **Thank you to the mentors, sponsors, and jury of the WiDS Datathon for their invaluable support.**
        """
    )

def upload_data_page():
    st.title("📂 Upload Data")
    uploaded_file = st.file_uploader("Upload your dataset (CSV/XLSX)", type=["csv", "xlsx"],
                                     help="Upload a CSV or Excel file containing your data.")
    if uploaded_file:
        try:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith(".csv") else pd.read_excel(uploaded_file)
            df = df.loc[:, ~df.columns.duplicated()]
            st.session_state.df = df
            st.write("**Preview of Uploaded Data:**")
            st.dataframe(df.head())
        except Exception as e:
            logging.error("Error loading uploaded file", exc_info=True)
            st.error(f"Error loading file: {e}")
    else:
        st.info("Please upload a dataset to continue.")

def explore_data_page():
    st.title("📊 Explore Data & Prepare")
    df = st.session_state.df
    if df is not None:
        st.subheader("Select Columns for Visualization")
        selected_cols = st.multiselect("Choose one or more columns to visualize:", df.columns.tolist())
        if selected_cols:
            for col in selected_cols:
                st.markdown(f"### Visualization for **{col}**")
                if pd.api.types.is_numeric_dtype(df[col]):
                    chart = alt.Chart(df).mark_bar().encode(
                        alt.X(f"{col}:Q", bin=alt.Bin(maxbins=20), title=col),
                        alt.Y("count()", title="Count"),
                        tooltip=[col, "count()"]
                    ).properties(width=600, height=400, title=f"Histogram of {col}")
                    st.altair_chart(chart, use_container_width=True)
                else:
                    counts = df[col].value_counts().reset_index()
                    counts.columns = [col, 'Count']
                    chart = alt.Chart(counts).mark_bar().encode(
                        alt.X(f"{col}:N", sort='-y', title=col),
                        alt.Y("Count:Q", title="Count"),
                        tooltip=[col, "Count"]
                    ).properties(width=600, height=400, title=f"Bar Chart of {col}")
                    st.altair_chart(chart, use_container_width=True)
        else:
            st.info("Please select at least one column for visualization.")

        st.markdown("### Missing Values Summary")
        missing = df.isnull().sum().reset_index()
        missing.columns = ['Column', 'Missing Values']
        st.dataframe(missing)

        st.markdown("### Disease and Gender Visualization")
        disease_header = st.selectbox("Select Disease Column for Visualization:", df.columns.tolist())
        gender_header = st.selectbox("Select Gender Column for Visualization:", df.columns.tolist())
        age_header = st.selectbox("Select Age Column (optional):", [None] + df.columns.tolist())
        if age_header:
            df[age_header] = pd.to_numeric(df[age_header], errors='coerce')

        if disease_header and gender_header:
            selected_disease = st.selectbox("Select Disease Category:", options=sorted(df[disease_header].dropna().unique()))
            filtered_df = df[df[disease_header] == selected_disease]
            if filtered_df.empty:
                st.info("No records found for the selected disease category.")
            else:
                # Pre-aggregate gender counts for the pie chart.
                gender_counts = filtered_df[gender_header].value_counts().reset_index()
                gender_counts.columns = [gender_header, "Count"]
                pie_chart = px.pie(
                    gender_counts,
                    names=gender_header,
                    values="Count",
                    title=f"Gender Distribution for {selected_disease}",
                    color=gender_header,
                    color_discrete_map={"F": "pink", "M": "blue"},
                    hover_data=["Count"]
                )
                # Show percentage inside slices.
                pie_chart.update_traces(texttemplate='%{percent:.1%}', textposition='inside')
                st.plotly_chart(pie_chart, use_container_width=True)

            # Add a slider to limit the sample size for the pivot table.
            sample_size = st.slider("Select number of rows for pivot table", min_value=100, max_value=len(df), value=min(1000, len(df)))
            df_sample = df.head(sample_size)
            pivot_df = pd.pivot_table(df_sample, index=disease_header, columns=gender_header, aggfunc='size', fill_value=0)
            for col in ["F", "M"]:
                if col not in pivot_df.columns:
                    pivot_df[col] = 0
            ordered_cols = ["F", "M"] + [c for c in pivot_df.columns if c not in ["F", "M"]]
            pivot_df = pivot_df[ordered_cols]

            if age_header:
                age_stats = df.groupby(disease_header)[age_header].agg(['mean', 'min', 'max']).rename(
                    columns={'mean': 'Avg Age', 'min': 'Min Age', 'max': 'Max Age'})
                pivot_df = pivot_df.merge(age_stats, left_index=True, right_index=True, how="left")

            def style_row(row):
                styles = []
                for col in row.index:
                    if col == "F":
                        styles.append("background-color: yellow; font-weight: bold;" if row[col] == 0 else "background-color: pink; color: black;")
                    elif col == "M":
                        styles.append("background-color: yellow; font-weight: bold;" if row[col] == 0 else "background-color: blue; color: white;")
                    else:
                        styles.append("")
                return styles

            st.markdown("### Disease & Gender Table with Age Metrics")
            if pivot_df.size > 262144:
                st.write("Pivot table too large to style. Displaying unstyled table:")
                st.dataframe(pivot_df)
            else:
                pivot_styled = pivot_df.style.apply(style_row, axis=1)
                st.dataframe(pivot_styled)
        else:
            st.info("Please select the appropriate Disease and Gender columns for visualization.")
    else:
        st.info("No data uploaded. Please use the Upload Data page.")

def model_prediction_page():
    st.title("🤖 Model Prediction")
    st.markdown("Select an AI model and upload chest X‑ray images for prediction.")
    model_choice = st.selectbox("Select AI Model:", ["CheXNet", "CheXagent"], help="Choose the model to use for prediction.")
    uploaded_images = st.file_uploader("Upload X‑ray Images", type=["png", "jpg", "jpeg"], accept_multiple_files=True, help="Upload one or more images.")
    fixed_threshold = 0.5  # Fixed threshold for CheXNet
    if uploaded_images:
        with st.spinner("Processing images..."):
            progress_bar = st.progress(0)
            total_images = len(uploaded_images)
            for i, img in enumerate(uploaded_images, start=1):
                st.write(f"Processing Image {i}/{total_images}")
                st.image(img, caption=f"Uploaded: {img.name}", width=300)
                try:
                    image = Image.open(img).convert("RGB")
                    if model_choice == "CheXNet":
                        tensor_img = preprocess_image(image)
                        tensor_img = tensor_img.to(device)
                        with torch.no_grad():
                            logits = chexnet_model(tensor_img)
                            probs = F.softmax(logits, dim=1)
                            disease_prob = probs[0, 1].item()
                            predicted_binary = 1 if disease_prob >= fixed_threshold else 0
                        pred_disease = "Disease Detected" if predicted_binary == 1 else "No Disease"
                        new_row = {"Image_ID": img.name, "Gender": "Unknown", "Prediction": pred_disease, "Probability": disease_prob}
                        st.session_state.df_results = pd.concat([st.session_state.df_results, pd.DataFrame([new_row])], ignore_index=True)
                        st.success(f"CheXNet Prediction: {pred_disease} | Prob: {disease_prob:.2%}")
                    elif model_choice == "CheXagent":
                        prompt = "Analyze the chest X‑ray image and return what you see along with the disease name detected."
                        response = chexagent_inference(image, prompt=prompt)
                        new_row = {"Image_ID": img.name, "Gender": "Unknown", "Prediction": response, "Probability": None}
                        st.session_state.df_results = pd.concat([st.session_state.df_results, pd.DataFrame([new_row])], ignore_index=True)
                        st.success(f"CheXagent Response: {response}")
                except Exception as e:
                    logging.error("Error during prediction", exc_info=True)
                    st.error(f"Error making prediction: {e}")
                progress_bar.progress(int((i / total_images) * 100))
    else:
        st.info("Upload images to generate predictions.")

def gender_bias_analysis_page():
    st.title("⚖️ Gender Bias Analysis")
    df = st.session_state.df
    df_results = st.session_state.df_results
    if df is None or df_results.empty:
        st.info("No prediction data available yet.")
    else:
        disease_col = st.selectbox("Select Ground-Truth Disease Column:", df.columns.tolist())
        image_id_col = st.selectbox("Select Image ID Column:", df.columns.tolist())
        if disease_col and image_id_col:
            df[disease_col] = df[disease_col].apply(unify_disease_label)
            merged = pd.merge(df_results, df[[image_id_col, disease_col]], how="left", left_on="Image_ID", right_on=image_id_col)
            merged = merged.rename(columns={disease_col: "True_Label"})
            merged["Correct"] = merged.apply(lambda row: row["Prediction"].strip().lower() == row["True_Label"].strip().lower(), axis=1)
            correct_counts = merged.groupby("True_Label")["Correct"].mean().reset_index()
            st.markdown("### Accuracy per Disease")
            st.dataframe(correct_counts)
            st.markdown("### Merged Predictions with Ground Truth")
            st.dataframe(merged.head())
        else:
            st.info("Please select the required columns for bias analysis.")

def bias_mitigation_simulation_page():
    st.title("🛠️ Bias Mitigation & Simulation")
    st.markdown("### Advanced Fairness Analysis")
    df = st.session_state.df
    df_results = st.session_state.df_results
    if df is None or df_results.empty:
        st.info("No prediction data available yet.")
    else:
        advanced = st.checkbox("Use advanced fairness approach", help="Enable advanced fairness metrics computation.")
        if advanced:
            image_id_col = st.selectbox("Select Image ID Column (Advanced):", df.columns.tolist())
            target_col = st.selectbox("Select Ground-Truth Disease Column (Advanced):", df.columns.tolist(), help="Choose the column with true disease labels.")
            sensitive_col = st.selectbox("Select Sensitive Attribute (Advanced):", df.columns.tolist(), help="Choose the sensitive attribute (e.g., Gender).")
            if image_id_col and target_col and sensitive_col:
                try:
                    df_merged = pd.merge(df, df_results, how="inner", left_on=image_id_col, right_on="Image_ID")
                    df_merged[target_col] = df_merged[target_col].apply(unify_disease_label)
                    y_true = df_merged[target_col]
                    y_pred = df_merged["Prediction"].str.lower().str.strip()
                    correct = y_true == y_pred
                    df_merged["Correct"] = correct
                    accuracy = correct.mean()
                    st.write(f"**Overall Accuracy:** {accuracy:.2%}")
                except Exception as e:
                    logging.error("Error computing advanced metrics", exc_info=True)
                    st.error(f"Error computing metrics: {e}")
        st.markdown("---")
        st.markdown("### Mitigation Approaches")
        st.markdown("**(List mitigation strategies here)**")
        st.success("Mitigation recommendations complete!")

def gender_bias_testing_page():
    st.title("🧪 Gender Bias Testing")
    st.markdown("### Test Bias Mitigation")
    df_results = st.session_state.df_results
    if df_results.empty:
        st.info("No prediction data available. Generate predictions first.")
    else:
        fixed_threshold = 0.5
        df_new = df_results.copy()
        df_new["Adjusted_Prediction"] = df_new["Prediction"]
        st.markdown("#### Predictions Preview")
        st.dataframe(df_new.head())

def explainable_analysis_page():
    st.title("🔍 Explainable Analysis")
    st.markdown("This page analyzes textual features related to false predictions to help understand potential bias.")
    df = st.session_state.df
    df_results = st.session_state.df_results
    if df is None or df_results.empty:
        st.info("No prediction data available.")
        return
    disease_col = st.selectbox("Select Ground-Truth Disease Column for Analysis:", df.columns.tolist())
    image_id_col = st.selectbox("Select Image ID Column for Analysis:", df.columns.tolist())
    if disease_col and image_id_col:
        merged = pd.merge(df_results, df[[image_id_col, disease_col]], how="left", left_on="Image_ID", right_on=image_id_col)
        merged = merged.rename(columns={disease_col: "True_Label"})
        merged["Correct"] = merged.apply(lambda row: row["Prediction"].strip().lower() == row["True_Label"].strip().lower(), axis=1)
        st.write("Merged Predictions with Ground Truth:")
        st.dataframe(merged.head())
    else:
        st.info("Please select the required columns for analysis.")

def importance_gender_bias_page():
    st.title("📚 The Importance of Gender Bias")
    st.markdown(
        """
        **Addressing Gender Bias is Critical:**

        - Fair treatment is a moral imperative.
        - Biased models risk misdiagnosis.
        - Fairness is essential from a regulatory perspective.
        - Underrepresentation leads to poorer outcomes.
        """
    )
    st.markdown("### References")
    st.markdown(
        """
        - [Mehrabi et al. (2021)](https://arxiv.org/abs/1908.09635)
        - [Obermeyer et al. (2019)](https://www.science.org/doi/10.1126/science.aax2342)
        - [Larrazabal et al. (2020)](https://www.nature.com/articles/s41467-020-19109-9)
        """
    )

def about_chexnet_model_page():
    st.title("🧠 About CheXNet Model")
    st.markdown(
        """
        **CheXNet** is based on **DenseNet-121**.

        - Dataset: ChestX-ray14
        - Architecture: Convolutional Neural Network (CNN)
        - Performance: Comparable to radiologists for pneumonia detection.
        - Paper: [CheXNet: Radiologist-Level Pneumonia Detection](https://arxiv.org/abs/1711.05225)
        """
    )

def about_chexagent_page():
    st.title("🧠 About CheXagent")
    st.markdown(
        """
        **CheXagent** is a chest X‑ray analysis model provided by Stanford AIMI on Hugging Face.

        - Model: StanfordAIMI/CheXagent-2-3b (integrated for image analysis and disease detection)
        """
    )

def meet_the_team_page():
    st.title("👥 Meet the Team")
    team_members = [
        {"name": "Yuying", "role": "Data Scientist", "image": os.path.join("images", "Yuying.webp")},
        {"name": "Siwen", "role": "ML Engineer", "image": os.path.join("images", "Siwen.webp")},
        {"name": "Zhi", "role": "Research Analyst", "image": os.path.join("images", "Zhi.webp")},
        {"name": "Maude", "role": "UX Designer", "image": os.path.join("images", "Maude.webp")}
    ]
    tabs = st.tabs([member["name"] for member in team_members])
    for tab, member in zip(tabs, team_members):
        with tab:
            poster_path = member["image"]
            if os.path.exists(poster_path):
                st.image(poster_path, width=150)
            else:
                st.error(f"Image not found: {poster_path}")
            st.write(f"**{member['name']}**")
            st.write(f"*{member['role']}*")

def chatbot_page():
    st.title("💬 Chatbot")
    st.markdown("Ask questions about gender bias in radiology. This chatbot provides predefined answers.")
    if "chat_history" not in st.session_state:
         st.session_state.chat_history = []
    with st.form("chat_form", clear_on_submit=True):
         user_message = st.text_input("Your question:", key="chat_message", help="For example, 'What is gender bias?'")
         submitted = st.form_submit_button("Send")
         if submitted and user_message:
             response = static_chatbot(user_message)
             st.session_state.chat_history.append(("You", user_message))
             st.session_state.chat_history.append(("Chatbot", response))
    st.markdown("### Conversation")
    for speaker, message in st.session_state.chat_history:
         st.markdown(f"**{speaker}:** {message}")

def posters_page():
    st.title("🖼️ Posters")
    st.markdown("Below are our project posters:")
    # Only display posters 1, 4, and 5.
    poster_files = ["1.png", "4.png", "5.png"]
    cols = st.columns(len(poster_files))
    for i, poster in enumerate(poster_files):
        with cols[i]:
            poster_path = os.path.join("images", poster)
            if os.path.exists(poster_path):
                st.image(poster_path, caption=f"Poster {poster.split('.')[0]}")
            else:
                st.error(f"Image not found: {poster_path}")

def datathon_resources_page():
    st.title("📖 Datathon Resources")
    st.markdown(
        """
        **WiDS Datathon 2025 Resources**

        - **Theme:** Harnessing AI Safely: Addressing the Challenges of Autonomous Systems
        - **Guidelines & Schedule:** [Datathon Guidelines & Schedule](https://em-lyon.com/en/women-in-data-science)
        - **Event Website:** [WiDS Datathon 2025](https://em-lyon.com/en/women-in-data-science)
        - **Sponsor & Contact Information:** Refer to the event materials provided by emlyon and partners.
        """
    )

def project_overview_page():
    st.title("📈 Project Overview")
    st.markdown(
        """
        **Project Objective:**

        To explore and mitigate gender bias in AI-driven chest X‑ray analysis while promoting responsible AI practices.

        **Impact:**

        - Enhance fairness in radiological predictions.
        - Promote ethical and safe AI development.
        - Contribute actionable mitigation strategies aligned with the datathon theme.

        **Approach:**

        - Comprehensive data exploration and visualization.
        - Comparison of multiple AI models.
        - Bias analysis and explainable analysis.
        """
    )

def feedback_page():
    st.title("📝 Feedback")
    st.markdown("We value your input! Please share your thoughts and suggestions below:")
    feedback = st.text_area("Your Feedback", help="Enter your comments here...")
    if st.button("Submit Feedback"):
        st.success("Thank you for your feedback!")

def interactive_demos_page():
    st.title("🔍 Interactive Demonstrations")
    st.markdown("Compare predictions from CheXNet and CheXagent side by side.")
    uploaded_image = st.file_uploader("Upload a single X‑ray image", type=["png", "jpg", "jpeg"])
    if uploaded_image:
        image = Image.open(uploaded_image).convert("RGB")
        st.image(image, caption="Uploaded X‑ray", width=300)
        # CheXNet prediction
        tensor_img = preprocess_image(image)
        tensor_img_chexnet = tensor_img.to(device)
        with torch.no_grad():
            logits = chexnet_model(tensor_img_chexnet)
            probs = F.softmax(logits, dim=1)
            chexnet_prob = probs[0, 1].item()
            chexnet_pred = "Disease Detected" if chexnet_prob >= 0.5 else "No Disease"
        # CheXagent prediction using our inference function.
        prompt = "Analyze the chest X‑ray image and return what you see along with the disease name detected."
        chexagent_response = chexagent_inference(image, prompt=prompt)
        st.markdown("### CheXNet Prediction")
        st.write(f"Prediction: {chexnet_pred}")
        st.write(f"Probability: {chexnet_prob:.2%}")
        st.markdown("### CheXagent Response")
        st.write(chexagent_response)
    else:
        st.info("Upload an image for comparison.")

def live_metrics_dashboard_page():
    st.title("📊 Live Metrics Dashboard")
    st.markdown("This dashboard displays key metrics from current predictions.")
    df_results = st.session_state.df_results
    if df_results.empty:
        st.info("No prediction data available yet.")
    else:
        total = df_results.shape[0]
        st.write(f"**Total Predictions:** {total}")
        pred_counts = df_results["Prediction"].value_counts()
        st.write("**Prediction Distribution:**")
        st.dataframe(pred_counts)
        if "Gender" in df_results.columns:
            total_F = df_results[df_results["Gender"]=="F"].shape[0]
            total_M = df_results[df_results["Gender"]=="M"].shape[0]
            F_disease = df_results[(df_results["Gender"]=="F") & (df_results["Prediction"]=="Disease Detected")].shape[0]
            M_disease = df_results[(df_results["Gender"]=="M") & (df_results["Prediction"]=="Disease Detected")].shape[0]
            rate_F = F_disease/total_F if total_F > 0 else 0
            rate_M = M_disease/total_M if total_M > 0 else 0
            st.write(f"**Female Detection Rate:** {rate_F:.2%} ({total_F} images)")
            st.write(f"**Male Detection Rate:** {rate_M:.2%} ({total_M} images)")
            st.write(f"**Bias Difference:** {abs(rate_F - rate_M):.4f}")
        chart_data = df_results[["Probability"]]
        st.line_chart(chart_data)

# ------------------------- SIDEBAR NAVIGATION -------------------------
page_options = [
    "🏠 Home",
    "📂 Upload Data",
    "📊 Explore Data & Prepare",
    "🤖 Model Prediction",
    "⚖️ Gender Bias Analysis",
    "🛠️ Bias Mitigation & Simulation",
    "🧪 Gender Bias Testing",
    "🔍 Explainable Analysis",
    "💬 Chatbot",
    "🖼️ Posters",
    "📚 The Importance of Gender Bias",
    "🧠 About CheXNet Model",
    "🧠 About CheXagent",
    "👥 Meet the Team",
    "📖 Datathon Resources",
    "📈 Project Overview",
    "📝 Feedback",
    "🔍 Interactive Demonstrations",
    "📊 Live Metrics Dashboard"
]

selected_page = st.sidebar.radio("Navigate to", page_options, help="Select a section to explore.")

# ------------------------- PAGE RENDERING -------------------------
if selected_page == "🏠 Home":
    home_page()
elif selected_page == "📂 Upload Data":
    upload_data_page()
elif selected_page == "📊 Explore Data & Prepare":
    explore_data_page()
elif selected_page == "🤖 Model Prediction":
    model_prediction_page()
elif selected_page == "⚖️ Gender Bias Analysis":
    gender_bias_analysis_page()
elif selected_page == "🛠️ Bias Mitigation & Simulation":
    bias_mitigation_simulation_page()
elif selected_page == "🧪 Gender Bias Testing":
    gender_bias_testing_page()
elif selected_page == "🔍 Explainable Analysis":
    explainable_analysis_page()
elif selected_page == "💬 Chatbot":
    chatbot_page()
elif selected_page == "🖼️ Posters":
    posters_page()
elif selected_page == "📚 The Importance of Gender Bias":
    importance_gender_bias_page()
elif selected_page == "🧠 About CheXNet Model":
    about_chexnet_model_page()
elif selected_page == "🧠 About CheXagent":
    about_chexagent_page()
elif selected_page == "👥 Meet the Team":
    meet_the_team_page()
elif selected_page == "📖 Datathon Resources":
    datathon_resources_page()
elif selected_page == "📈 Project Overview":
    project_overview_page()
elif selected_page == "📝 Feedback":
    feedback_page()
elif selected_page == "🔍 Interactive Demonstrations":
    interactive_demos_page()
elif selected_page == "📊 Live Metrics Dashboard":
    live_metrics_dashboard_page()