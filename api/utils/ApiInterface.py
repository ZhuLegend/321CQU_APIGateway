import inspect
from functools import wraps
from typing import Callable, Type, TypeVar, Generic, Dict, Optional, Union

from sanic.views import HTTPMethodView
from sanic.response import json as sanic_json

from sanic_ext.exceptions import InitError
from sanic_ext.extensions.openapi.builders import OperationStore
from sanic_ext.utils.extraction import extract_request

from pydantic import BaseModel, ValidationError
from pydantic.generics import GenericModel

from grpc.aio import AioRpcError

from utils.Exceptions import _321CQUException

__all__ = ['ApiInterface', 'api_request', 'ApiResponse', 'api_response', 'handle_grpc_error']

T = TypeVar("T")


class BaseApiResponse(GenericModel, Generic[T]):
    """
    API响应模版，回传的ApiResponse子类会填入data中
    """
    status: int
    msg: str
    data: T | None


class ApiResponse(BaseModel):
    """
    API响应公共父类，所有响应模型请继承该类
    """
    pass


def api_request(
        json: Type[BaseModel] | None = None,
        form: Type[BaseModel] | None = None,
        query: Type[BaseModel] | None = None,
        body_argument: str = "body",
        query_argument: str = "query",
        **kwargs
) -> Callable[[T], T]:
    """
    实现请求参数校验即OpenAPI文档生成的装饰器

    **json和form不能同时被设置**

    :param json: 请求体以json格式读取后应当对应的数据模型
    :param form: 请求体以form格式读取后应当对应的数据模型
    :param query: 路由查询参数读取后应对应的数据模型
    :param body_argument: 请求体中参数应当注入的名字，默认为"body"
    :param query_argument: 路由查询参数应当注入的名字，默认为"body"
    :param kwargs: 其他需要显示在/docs中的参数（需满足OpenAPI规范）
    """
    schemas: Dict[str, BaseModel] = {
        key: param
        for key, param in (
            ("json", json),
            ("form", form),
            ("query", query),
        )
    }

    if json and form:
        raise InitError("Cannot define both a form and json route validator")

    def decorator(f):
        body_content = {"application/json": json} if json is not None else \
            ({"application/x-www-form-urlencoded": form} if form else None)
        params = {**kwargs}

        @wraps(f)
        async def decorated_function(*args, **kwargs):
            request = extract_request(*args)
            try:
                if json:
                    kwargs[body_argument] = json.parse_obj(request.json)
                elif form:
                    kwargs[body_argument] = form.parse_obj(request.form)
                if query:
                    kwargs[query_argument] = query.parse_obj(request.args)
            except ValidationError as e:
                raise _321CQUException(error_info=f"请求参数错误，报错信息：{e.json()}", quiet=True)
            retval = f(*args, **kwargs)
            if inspect.isawaitable(retval):
                retval = await retval
            return retval
        # 使用sanic-ext中的OperationStore实现文档自动生成，参考其中的openapi.body装饰器
        if body_content is not None:
            if f in OperationStore():
                OperationStore()[decorated_function] = OperationStore().pop(f)
            OperationStore()[decorated_function].body(body_content, **params)
        return decorated_function

    return decorator


def api_response(retval: Optional[Union[Type[ApiResponse], Dict]] = None, status: int = 200, description: str = '',
                 **kwargs):
    """
    实现参数返回值自动包装与API页面生成的装饰器
    :param retval: 返回值信息，可以为ApiResponse的子类或字典，为空则返回值中data项置None
    :param status: Response Http相应码
    :param description: 返回值相关描述，显示在/docs页面中
    :param kwargs: 其他需要显示在/docs中的参数（需满足OpenAPI规范）
    """
    def decorator(f):
        @wraps(f)
        async def decorated_function(*args, **kwargs):
            ret = f(*args, **kwargs)
            if inspect.isawaitable(ret):
                ret = await ret

            kwargs["status"] = status
            return sanic_json(BaseApiResponse(status=1, msg='success', data=ret if ret is not None else {}).dict())

        # 使用sanic-ext中的OperationStore实现文档自动生成，参考其中的openapi.body装饰器
        if f in OperationStore():
            OperationStore()[decorated_function] = OperationStore().pop(f)

        if inspect.isclass(retval) and issubclass(retval, ApiResponse):
            OperationStore()[decorated_function].response(status, {'application/json': BaseApiResponse[retval]},
                                                          description, **kwargs)
        else:
            base_retval = {'status': 1, 'msg': 'success', 'data': retval if retval is not None else {}}
            OperationStore()[decorated_function].response(status, base_retval, description, **kwargs)
        return decorated_function

    return decorator


class ApiInterface(HTTPMethodView):
    """
    所有API调用的公共父类，API调用请继承该类
    """
    pass


def handle_grpc_error(func):
    """
    处理AioRpcError，返回的HttpResponse中包含503与grpc报错详细信息
    """
    @wraps(func)
    async def wrapped_function(*args, **kwargs):
        try:
            ret = func(*args, **kwargs)
            if inspect.isawaitable(ret):
                ret = await ret
            return ret
        except AioRpcError as e:
            raise _321CQUException(error_info="服务调用异常", extra=e.details(), status_code=503)
    return wrapped_function
