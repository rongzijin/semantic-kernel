# Copyright (c) Microsoft. All rights reserved.

import logging
from typing import Any, List, Optional, Tuple

import pydantic as pdt

from semantic_kernel.orchestration.kernel_function_base import KernelFunctionBase
from semantic_kernel.plugin_definition.read_only_plugin_collection_base import (
    ReadOnlyPluginCollectionBase,
)
from semantic_kernel.template_engine.blocks.block import Block
from semantic_kernel.template_engine.blocks.block_types import BlockTypes
from semantic_kernel.template_engine.blocks.function_id_block import FunctionIdBlock
from semantic_kernel.template_engine.code_tokenizer import CodeTokenizer

logger: logging.Logger = logging.getLogger(__name__)


class CodeBlock(Block):
    _tokens: List[Block] = pdt.PrivateAttr()
    _validated: bool = pdt.PrivateAttr(default=False)

    def __init__(
        self,
        content: str,
        tokens: Optional[List[Block]] = None,
        log: Optional[Any] = None,
    ):
        super().__init__(content=content and content.strip())

        if log:
            logger.warning("The `log` parameter is deprecated. Please use the `logging` module instead.")

        self._tokens = tokens or CodeTokenizer().tokenize(content)
        self._validated = False

    @property
    def type(self) -> BlockTypes:
        return BlockTypes.CODE

    def is_valid(self) -> Tuple[bool, str]:
        error_msg = ""

        for token in self._tokens:
            is_valid, error_msg = token.is_valid()
            if not is_valid:
                logger.error(error_msg)
                return False, error_msg

        if len(self._tokens) > 1:
            if self._tokens[0].type != BlockTypes.FUNCTION_ID:
                error_msg = f"Unexpected second token found: {self._tokens[1].content}"
                logger.error(error_msg)
                return False, error_msg

            if self._tokens[1].type != BlockTypes.VALUE and self._tokens[1].type != BlockTypes.VARIABLE:
                error_msg = "Functions support only one parameter"
                logger.error(error_msg)
                return False, error_msg

        if len(self._tokens) > 2:
            error_msg = f"Unexpected second token found: {self._tokens[1].content}"
            logger.error(error_msg)
            return False, error_msg

        self._validated = True

        return True, ""

    async def render_code(self, context):
        if not self._validated:
            is_valid, error = self.is_valid()
            if not is_valid:
                raise ValueError(error)

        logger.debug(f"Rendering code: `{self.content}`")

        if self._tokens[0].type in (BlockTypes.VALUE, BlockTypes.VARIABLE):
            return self._tokens[0].render(context.variables)

        if self._tokens[0].type == BlockTypes.FUNCTION_ID:
            return await self._render_function_call(self._tokens[0], context)

        raise ValueError(f"Unexpected first token type: {self._tokens[0].type}")

    async def _render_function_call(self, f_block: FunctionIdBlock, context):
        if not context.plugins:
            raise ValueError("Plugin collection not set")

        function = self._get_function_from_plugin_collection(context.plugins, f_block)

        if not function:
            error_msg = f"Function `{f_block.content}` not found"
            logger.error(error_msg)
            raise ValueError(error_msg)

        variables_clone = context.variables.clone()

        if len(self._tokens) > 1:
            logger.debug(f"Passing variable/value: `{self._tokens[1].content}`")
            input_value = self._tokens[1].render(variables_clone)
            variables_clone.update(input_value)

        result = await function.invoke_async(variables=variables_clone, memory=context.memory)

        if result.error_occurred:
            error_msg = (
                f"Function `{f_block.content}` execution failed. "
                f"{result.last_exception.__class__.__name__}: "
                f"{result.last_error_description}"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        return result.result

    def _get_function_from_plugin_collection(
        self, plugins: ReadOnlyPluginCollectionBase, f_block: FunctionIdBlock
    ) -> Optional[KernelFunctionBase]:
        if not f_block.plugin_name and plugins.has_function(None, f_block.function_name):
            return plugins.get_function(None, f_block.function_name)

        if f_block.plugin_name and plugins.has_function(f_block.plugin_name, f_block.function_name):
            return plugins.get_function(f_block.plugin_name, f_block.function_name)

        return None
