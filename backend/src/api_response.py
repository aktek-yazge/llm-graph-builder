


from src.utf8_utils import ensure_utf8_encoding

def create_api_response(status,success_count=None,failed_count=None, data=None, error=None,message=None,file_source=None,file_name=None):
    """
    Create a response to be sent to the API. This is a helper function to create a JSON response that can be sent to the API.
    
    Args:
        status: The status of the API call. Should be one of the constants in this module.
        data: The data that was returned by the API call.
        error: The error that was returned by the API call.
        success_count: Number of files successfully processed.
        failed_count: Number of files failed to process.
    Returns: 
      A dictionary containing the status data and error if any
    """
    response = {"status": status}

    # Set the data of the response - UTF-8 normalized
    if data is not None:
      response["data"] = ensure_utf8_encoding(data)

    # Set the error message to the response - UTF-8 normalized
    if error is not None:
      response["error"] = ensure_utf8_encoding(error)
    
    if success_count is not None:
      response['success_count']=success_count
      response['failed_count']=failed_count
    
    if message is not None:
        response['message'] = ensure_utf8_encoding(message)
    
    if file_source is not None:
        response['file_source'] = ensure_utf8_encoding(file_source)
        
    if file_name is not None:
        response['file_name'] = ensure_utf8_encoding(file_name)

    return response