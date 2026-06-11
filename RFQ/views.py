import json
import re
import os
from django.shortcuts import render
from django.http import JsonResponse
from django.core.files.storage import FileSystemStorage
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt

from .models import Material, DrawingAnalysis, DrawingDetectedMaterial, PartComponent
from .forms import DrawingUploadForm

from RFQ.services.parsers.pdf_parser import (
    extract_text_from_pdf, extract_volume, extract_weight, extract_color
)
from RFQ.services.ai.structured_extractor import extract_rfq_data
from RFQ.services.materials.embedding_matcher import match_material

from RFQ.services.cad.step_parser import analyze_step
from RFQ.services.cad.stl_parser import analyze_stl
from django.db.models import Q
from .tasks import process_file_in_background
def upload_and_process_rfq(request):
    context = {}
    form = DrawingUploadForm()
    
    history_id = request.GET.get('history_id')
    if history_id:
        try:
            past_analysis = DrawingAnalysis.objects.get(id=history_id)
            detected_items = past_analysis.detected_materials.all()
            for item in detected_items:
                if not item.detected_material and item.detected_family != "N/D":
                    clean_family = item.detected_family.replace("Resin", "").strip()
                    item.sugerencias = Material.objects.filter(family__icontains=clean_family[:4])[:3]
                else:
                    item.sugerencias = None
            
            past_classifications = []
            for item in detected_items:
                if "Origen:" in item.bom_reference:
                    orig = item.bom_reference.split("Origen:")[-1].strip()
                    if orig not in past_classifications:
                        past_classifications.append(orig)
            
            context['analysis'] = past_analysis
            context['detected_components'] = detected_items
            context['file_classifications'] = past_classifications
            context['filename'] = past_analysis.uploaded_file.name if past_analysis.uploaded_file else "Archivo Histórico"
        except DrawingAnalysis.DoesNotExist:
            context['error'] = "El análisis histórico solicitado no existe."

    if request.method == 'POST' and request.FILES.getlist('rfq_file'):
        uploaded_files = request.FILES.getlist('rfq_file')
        fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'tmp'))
        
        primary_pdf = None
        all_saved_files = []
        
        for u_file in uploaded_files:
            filename = fs.save(u_file.name, u_file)
            file_path = fs.path(filename)
            ext = os.path.splitext(u_file.name)[1].lower()
            all_saved_files.append({'name': u_file.name, 'path': file_path, 'ext': ext})
            if ext == '.pdf' and not primary_pdf:
                primary_pdf = u_file

        if not primary_pdf and uploaded_files:
            primary_pdf = uploaded_files[0]

        analysis = DrawingAnalysis.objects.create(
            uploaded_file=primary_pdf,
            raw_text="",
            gemini_raw_json={},
            status='processing'
        )

        return JsonResponse({
            'status': 'initiated',
            'analysis_id': analysis.id,
            'files': all_saved_files
        })

    context['all_materials_catalog'] = Material.objects.all().order_by('material_code')
    
    context['past_analyses'] = DrawingAnalysis.objects.all().order_by('-uploaded_at')[:20]
    context['form'] = form
    return render(request, 'RFQ/upload.html', context)

@csrf_exempt
def process_single_file_async(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
        
    try:
        data = json.loads(request.body)
        analysis_id = data.get('analysis_id')
        file_name = data.get('file_name')
        file_path = data.get('file_path')
        ext = data.get('ext')
        is_subcomponent_manual = data.get('is_subcomponent', False)
        
        # Disparamos la tarea en segundo plano usando Celery (.delay)
        process_file_in_background.delay(
            analysis_id, file_name, file_path, ext, is_subcomponent_manual
        )
        
        # Respondemos de inmediato al navegador que la tarea ya está en cola
        return JsonResponse({
            'success': True, 
            'status': 'queued', 
            'message': 'El archivo se está procesando mediante IA en segundo plano.'
        })

    except Exception as general_err:
        return JsonResponse({'success': False, 'error': str(general_err)}, status=500)
    
    
@csrf_exempt
def finalize_analysis_status(request):
    """
    Consolida el estado final de la auditoría. Configura la nomenclatura específica
    multi-maestro e introduce el campo descriptivo final de los números de parte.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Método inválido'}, status=400)
        
    try:
        data = json.loads(request.body)
        analysis_id = data.get('analysis_id')
        analysis = DrawingAnalysis.objects.get(id=analysis_id)
        detected_items = analysis.detected_materials.all()
        
        total_validos = 0
        archivos_maestros = []
        partes_sistema = []
        
        for item in detected_items:
            if item.component_weight or item.detected_material:
                total_validos += 1
                
            if item.part_number and item.part_number != "Insumo / Adicional":
                if item.part_number not in partes_sistema:
                    partes_sistema.append(item.part_number)
            
            if "Origen:" in item.bom_reference:
                orig_file = item.bom_reference.split("Origen:")[-1].split("|")[0].strip()
                if orig_file and orig_file not in archivos_maestros:
                    archivos_maestros.append(orig_file)

        if archivos_maestros:
            nombres_limpios = [os.path.splitext(f)[0] for f in archivos_maestros]
            nuevo_nombre = f"Planos: [{', '.join(nombres_limpios[:3])}]"
            if len(nombres_limpios) > 3:
                nuevo_nombre += "..."
        elif partes_sistema:
            nuevo_nombre = f"Partes: [{', '.join(partes_sistema[:3])}]"
        else:
            nuevo_nombre = f"Análisis Técnico Lote #{analysis.id}"

        analysis.material_text = nuevo_nombre
        analysis.raw_text = f"TITULO_LOTE: {nuevo_nombre}\n" + analysis.raw_text
        
        if total_validos == 0:
            analysis.status = 'failed'
        else:
            analysis.status = 'completed'
            
        analysis.save()
        return JsonResponse({
            'status': 'finalized',
            'final_status': analysis.status,
            'suggested_name': nuevo_nombre
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    
from django.http import JsonResponse
from .models import DrawingAnalysis

def check_analysis_status(request):
    """
    Endpoint ultrarrápido para que el frontend consulte cómo va la tarea de Celery.
    """
    analysis_id = request.GET.get('analysis_id')
    if not analysis_id:
        return JsonResponse({'error': 'Falta el ID'}, status=400)
        
    try:
        analysis = DrawingAnalysis.objects.get(id=analysis_id)
        return JsonResponse({
            'status': analysis.status, # Puede ser 'pending', 'processing', 'completed', o 'failed'
            'part_number': analysis.part_number
        })
    except DrawingAnalysis.DoesNotExist:
        return JsonResponse({'error': 'No existe'}, status=404)