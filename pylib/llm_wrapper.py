# SPDX-FileCopyrightText: 2023-present Oori Data <info@oori.dev>
# SPDX-License-Identifier: Apache-2.0
# ogbujipt.llm_wrapper
'''
Wrapper for LLMs, including via OpenAI API and locally hosted models

Side note: a lot of OpenAI tutorials suggest that you embed your
OpenAI private key into the code, which is a horrible, no-good idea

Extra reminder: If you set up your environment via .env file, make sure
it's in .gitignore or equivalent so it never gets accidentally committed!
'''

import os
import asyncio
import concurrent.futures
from functools import partial
from typing import List

from amara3 import iri

from ogbujipt import config

# Imports of model hosting facilities
try:
    import openai as openai_api_global
    DEFAULT_OPENAI_API_BASE = openai_api_global.api_base
except ImportError:
    openai_api_global = None

# In many cases of self-hosted models you just get whatever model is loaded, rather than specifying it in the API
DUMMY_MODEL = 'DUMMY_MODEL'


# FIXME: Should probably be an ABC
class llm_wrapper:
    '''
    Base-level wrapper for LLMs
    '''
    def __init__(self, model=None, **kwargs):
        '''
        Args:
            model (str): Name of the model being wrapped

            kwargs (dict, optional): Extra parameters for the API, the model, etc.
        '''
        self.model = model
        self.parameters = kwargs


# Extracted from https://github.com/openai/openai-python/blob/main/openai/__init__.py
OPENAI_GLOBALS = ['api_key', 'api_key_path', 'api_base', 'organization', 'api_type', 'api_version',
                 'proxy', 'app_info', 'debug', 'log']


class openai_api(llm_wrapper):
    '''
    Wrapper for LLM hosted via OpenAI-compatible API (including OpenAI proper).
    Designed for models that provide simple completions from prompt.
    For chat-style models (including OpenAI's gpt-3.5-turbo & gpt-4), use openai_chat_api
            
    >>> from ogbujipt.llm_wrapper import openai_api
    >>> llm_api = openai_api(api_base='http://localhost:8000')
    >>> resp = llm_api('Knock knock!', max_tokens=128)
    >>> llm_api.first_choice_text(resp)
    '''
    def __init__(self, model=None, api_base=None, api_key=None, **kwargs):
        '''
        If using OpenAI proper, you can pass in an API key, otherwise environment variable
        OPENAI_API_KEY will be checked

        This class is designed such that you can have multiple instances with different LLMs wrapped,
        perhaps one is OpenAI proper, another is a locally hosted model, etc. (a good way to save costs)
        This also opens up reentrancy concerns, especially if you're using multi-threading or multi-processing.

        If multi-processing, this class should bundle its settings correctly across the fork,
        and when you call methods in the child process, these settings should be swapped into global context correctly

        Args:
            model (str, optional): Name of the model being wrapped. Useful for using
            OpenAI proper, or any endpoint that allows you to select a model

            api_base (str, optional): Base URL of the API endpoint

            api_key (str, optional): OpenAI API key to use for authentication

            debug (bool, optional): Debug flag

        Returns:
            openai_api (openai): Prepared OpenAI API

        Args:
            model (str): Name of the model being wrapped

            kwargs (dict, optional): Extra parameters for the API, the model, etc.
        '''
        if openai_api_global is None:
            raise ImportError('openai module not available; Perhaps try: `pip install openai`')
        if api_key is None:
            api_key = os.getenv('OPENAI_API_KEY', config.OPENAI_KEY_DUMMY)

        self.api_key = api_key
        self.parameters = config.attr_dict(kwargs)
        if api_base:
            # If the user includes the API version in the base, don't add it again
            scheme, authority, path, query, fragment = iri.split_uri_ref(api_base)
            path = path or kwargs.get('api_version', '/v1')
            self.api_base = iri.unsplit_uri_ref((scheme, authority, path, query, fragment))
        else:
            self.api_base = DEFAULT_OPENAI_API_BASE
        self.original_model = model or None
        self.model = model or self.hosted_model()
        self._claim_global_context()

    # Nature of openai library requires that we babysit their globals
    def _claim_global_context(self):
        '''
        Set global context of the OpenAI API according to this class's settings
        '''
        for k in OPENAI_GLOBALS:
            if k in self.parameters:
                setattr(openai_api_global, k, self.parameters[k])
            # if hasattr(self.parameters, k):
            #     setattr(openai_api_global, k, getattr(self.parameters, k))
        openai_api_global.model = self.model
        openai_api_global.api_key = self.api_key
        openai_api_global.api_base = self.api_base

        # If the user didn't set the model, check what's being hosted each time
        if not self.original_model:
            # Introspect the model
            self.model = self.hosted_model()
            if 'logger' in self.parameters:
                self.parameters['logger'].debug(
                    f'Switching global context to query LLM hosted at {self.api_base}, model {self.model}')
        return

    def __call__(self, prompt, api_func=None, **kwargs):
        '''
        Invoke the LLM with a completion request

        Args:
            prompt (str): Prompt to send to the LLM

            kwargs (dict, optional): Extra parameters to pass to the model via API

        Returns:
            dict: JSON response from the LLM
        '''
        api_func = api_func or openai_api_global.Completion.create
        # Ensure the right context, e.g. after a fork or when using multiple LLM wrappers
        self._claim_global_context()
        merged_kwargs = {**self.parameters, **kwargs}
        result = api_func(model=self.model, prompt=prompt, **merged_kwargs)
        # print(result)
        if result.get('model') == 'HOSTED_MODEL':
            result['model'] = self.model
        return result

    def wrap_for_multiproc(self, prompt, **kwargs):
        '''
        Wrap the LLM invocation in an asyncio task

        Returns:
            asyncio.Task: Task for the LLM invocation
        '''
        merged_kwargs = {**self.parameters, **kwargs}
        # print(f'wrap_for_multiproc: merged_kwargs={merged_kwargs}')
        return asyncio.create_task(
            schedule_callable(self, prompt, **merged_kwargs))

    def hosted_model(self) -> str:
        '''
        Model introspection: Query the API to find what model is being run for LLM calls

        >>> from ogbujipt.llm_wrapper import openai_api
        >>> llm_api = openai_api(api_base='http://localhost:8000')
        >>> print(llm_api.hosted_model())
        '/models/TheBloke_WizardLM-13B-V1.0-Uncensored-GGML/wizardlm-13b-v1.0-uncensored.ggmlv3.q6_K.bin'
        '''
        models = self.available_models()
        if len(models) > 1:
            return models
        else:
            return self.model

    def available_models(self) -> List[str]:
        '''
        Query the API to find what model is being run for LLM calls

        Also includes model introspection, e.g.:

        >>> from ogbujipt.llm_wrapper import openai_api
        >>> llm_api = openai_api(api_base='http://localhost:8000')
        >>> print(llm_api.hosted_model())
        ['/models/TheBloke_WizardLM-13B-V1.0-Uncensored-GGML/wizardlm-13b-v1.0-uncensored.ggmlv3.q6_K.bin']
        '''
        try:
            import httpx  # noqa
        except ImportError:
            raise RuntimeError('Needs httpx installed. Try pip install httpx')

        resp = httpx.get(f'{self.api_base}/models').json()
        # print(resp)
        model_fullnames = next(iter((i['id'] for i in resp['data'])))
        return model_fullnames

    def first_choice_text(self, response):
        '''
        Given an OpenAI-compatible API simple completion response, return the first choice text
        '''
        try:
            return response['choices'][0]['text']
        except KeyError:
            raise RuntimeError(
                f'''Response does not appear to be an OpenAI API completion structure, as expected:
{repr(response)}''')


class openai_chat_api(openai_api):
    '''
    Wrapper for a chat-style LLM hosted via OpenAI-compatible API (including OpenAI proper).
    Supports local chat-style models as well as OpenAI's gpt-3.5-turbo & gpt-4

    You need to set an OpenAI API key in your environment, or pass it in, for this next example

        >>> from ogbujipt.llm_wrapper import openai_chat_api, prompt_to_chat
    >>> llm_api = openai_chat_api(model='gpt-3.5-turbo')
    >>> resp = llm_api(prompt_to_chat('Knock knock!'))
    >>> llm_api.first_choice_message(resp)
    '''
    def __call__(self, messages, api_func=None, **kwargs):
        '''
        Invoke the LLM with a completion request

        Args:
            messages (list): Series of messages representing the chat history
            e.f. messages=[{"role": "user", "content": "Hello world"}])

            kwargs (dict, optional): Extra parameters to pass to the model via API

        Returns:
            dict: JSON response from the LLM
        '''
        api_func = api_func or openai_api_global.ChatCompletion.create
        # Ensure the right context, e.g. after a fork or when using multiple LLM wrappers
        self._claim_global_context()
        return api_func(model=self.model, messages=messages, **self.parameters, **kwargs)

    def first_choice_message(self, response):
        '''
        Given an OpenAI-compatible API chat completion response, return the first choice message content
        '''
        try:
            return response['choices'][0]['message']['content']
        except KeyError:
            raise RuntimeError(
                f'''Response does not appear to be an OpenAI API chat-style completion structure, as expected:
{repr(response)}''')


class ctransformer:
    '''
    ctransformers wrapper for LLMs
    '''
    def __init__(self, model=None, **kwargs):
        '''
        Args:
            model (XYZ): Name of the model being wrapped

            kwargs (dict, optional): Extra parameters for the API, the model, etc.

        # gpu_layers - # of layers to offload to GPU. Set to 0 if no GPU acceleration is available on your system
        llm = AutoModelForCausalLM.from_pretrained(
            'TheBloke/LlongOrca-13B-16K-GGUF', model_file='llongorca-13b-16k.q4_K_M.gguf',
            model_type='llama', gpu_layers=50)

        ctrans_wrapper = ctrans_wrapper(model=llm)

        Defined model params from https://github.com/marella/ctransformers/blob/main/ctransformers/llm.py

        top_k="The top-k value to use for sampling.",
        top_p="The top-p value to use for sampling.",
        temperature="The temperature to use for sampling.",
        repetition_penalty="The repetition penalty to use for sampling.",
        last_n_tokens="The number of last tokens to use for repetition penalty.",
        seed="The seed value to use for sampling tokens.",
        max_new_tokens="The maximum number of new tokens to generate.",
        stop="A list of sequences to stop generation when encountered.",

        Operational params:

        stream="Whether to stream the generated text.",
        reset="Whether to reset the model state before generating text.",
        batch_size="The batch size to use for evaluating tokens in a single prompt.",
        threads="The number of threads to use for evaluating tokens.",
        context_length="The maximum context length to use.",
        gpu_layers="The number of layers to run on GPU.",
        '''
        # if AutoModelForCausalLM is None:
        #     raise ImportError('ctransformers module not available; Perhaps try: `pip install ctransformers`')
        if model is None:
            raise ValueError('Must supply a model')
        self.model = model
        self.parameters = kwargs

    def __call__(self, prompt, **kwargs):
        '''
        Invoke the LLM with a completion request

        Args:
            prompt (str): Prompt to send to the LLM

            kwargs (dict, optional): Extra parameters to pass to the model via API

        Returns:
            dict: JSON response from the LLM
        '''
        # return self.model.generate(prompt, **kwargs)
        # yield from self.model.generate(prompt, **kwargs)
        return self.model(prompt, **kwargs)


def prompt_to_chat(prompt):
    '''
    Convert a prompt string to a chat-style message list

    Args:
        prompt (str): Prompt to convert

    Returns:
        list: single item with just the given prompt as user message for chat history
        e.g. messages=[{"role": "user", "content": "Hello world"}])
    '''
    # return [{'role': 'user', 'content': m} for m in prompt.split('\n')]
    return [{'role': 'user', 'content': prompt}]


async def schedule_callable(callable, *args, **kwargs):
    '''
    Schedule long-running/blocking function call in a separate process,
    wrapped to work well in an asyncio event loop

    Basically hides away a bunch of the multiprocessing webbing

    e.g. `llm_task = asyncio.create_task(schedule_callable(llm, prompt))`

    Can then use asyncio.wait(), asyncio.gather(), etc. with `llm_task`

    Args:
        callable (callable): Callable to be scheduled

    Returns:
        response: Response object
    '''
    # print(f'schedule_callable: kwargs={kwargs}')
    # Link up the current async event loop for multiprocess execution
    loop = asyncio.get_running_loop()
    executor = concurrent.futures.ProcessPoolExecutor()
    # Need to partial execute to get in any kwargs for the target callable
    prepped_callable = partial(callable, **kwargs)
    # Spawn a separate process for the LLM call
    response = await loop.run_in_executor(executor, prepped_callable, *args)
    return response
