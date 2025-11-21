import os
import google.generativeai as genai
from dotenv import load_dotenv
from utils.helper_functions import num_tokens_from_string

load_dotenv()


class GeminiModel:
    def __init__(self, system_prompt, temperature):
        self.temperature = temperature
        self.system_prompt = system_prompt

        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        self.model_name = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=self.system_prompt,
        )

    def generate_text(self, prompt):
        try:
            input_tokens_length = num_tokens_from_string(self.system_prompt + prompt)
            print("input tokens length", input_tokens_length)

            generation_config = {
                "temperature": self.temperature,
                "response_mime_type": "application/json",
            }

            response = self.model.generate_content(
                prompt,
                generation_config=generation_config,
            )

            text = getattr(response, "text", "")
            if not text:
                try:
                    parts = response.candidates[0].content.parts
                    text = "".join(p.text for p in parts if hasattr(p, "text"))
                except Exception:
                    text = str(response)

            output_tokens_length = num_tokens_from_string(text)
            print("output tokens length", output_tokens_length)
            return text, input_tokens_length, output_tokens_length

        except Exception as e:
            response = {"error": f"Error in invoking gemini model! {str(e)}"}
            print(response)
            return response

    def generate_string_text(self, prompt):
        try:
            input_tokens_length = num_tokens_from_string(self.system_prompt + prompt)
            print("input tokens length", input_tokens_length)

            generation_config = {
                "temperature": self.temperature,
            }

            response = self.model.generate_content(
                prompt,
                generation_config=generation_config,
            )

            text = getattr(response, "text", "")
            if not text:
                try:
                    parts = response.candidates[0].content.parts
                    text = "".join(p.text for p in parts if hasattr(p, "text"))
                except Exception:
                    text = str(response)

            output_tokens_length = num_tokens_from_string(text)
            print("output tokens length", output_tokens_length)
            return text, input_tokens_length, output_tokens_length

        except Exception as e:
            response = {"error": f"Error in invoking gemini model! {str(e)}"}
            print(response)
            return response

    def generate_with_web_annotations(self, prompt, search_type="search"):
        try:
            search_results, _ = get_search_result(search_type, prompt)
            combined_texts, links = extract_data(search_results if isinstance(search_results, list) else [])
            search_context = "\n".join(combined_texts)

            composed_prompt = f"SEARCH_CONTEXT:\n{search_context}\n\nINPUT_TEXT:{prompt}\nOUTPUT:"
            input_tokens_length = num_tokens_from_string(composed_prompt)
            print("input tokens length", input_tokens_length)

            generation_config = {
                "temperature": self.temperature,
            }

            response = self.model.generate_content(
                composed_prompt,
                generation_config=generation_config,
            )

            text = getattr(response, "text", "")
            if not text:
                try:
                    parts = response.candidates[0].content.parts
                    text = "".join(p.text for p in parts if hasattr(p, "text"))
                except Exception:
                    text = str(response)

            output_tokens_length = num_tokens_from_string(text)
            print("output tokens length", output_tokens_length)

            return text, links, input_tokens_length, output_tokens_length

        except Exception as e:
            print(f"Error in generate_with_web_annotations: {str(e)}")
            return {"error": str(e)}